# HitterDNA Hit Probability v0 Contract

Status: planning and data-readiness only, 2026-09-07.

## Target and prediction-time boundary

The sole Projection v0 target is:

`P(at least one official hit in a game | information available before first pitch)`.

The prediction-time cutoff is the recorded prediction snapshot (`as_of_utc`),
which must be strictly before the official scheduled first-pitch timestamp.
Every pregame feature snapshot, source observation, and lineage record used for
inference must have been retrieved or valid at or before that cutoff. Outcome
labels follow the separate historical-record rule below. An official lineup is required,
but no prediction can be issued once the game is In Progress, Final, Game Over,
Completed Early, Postponed, Cancelled, Suspended, or otherwise not pregame.

Projection v0 is limited to official hits.  Home runs, total bases, runs, and
RBI are distinct future targets and are outside this contract.

`xBA` is a source-defined expected-batting-average observation.  It is not a
game hit probability and must not be mechanically converted into one.

## Labels and historical record

The training label is `1` when the official MLB Stats API box score records one
or more hits for a player in a completed game, otherwise `0` for an official
participant with zero hits.  Label source: `mlb_statsapi_box_scores`.

Each historical example requires canonical `game_pk`, `player_mlbam_id`,
`game_date`, and `season`, the final box-score source observation and raw hash,
and a retained pre-first-pitch feature snapshot for the same keys.  A final box
score may label an example; it can never replace its missing pregame snapshot
or establish a pregame lineup retroactively.

Final box-score provenance is outcome-label-only evidence and may be retrieved
after game completion. It is never evaluated as a pregame feature. Leakage
control instead requires a structured paired pregame snapshot with source
reference or endpoint URL, raw SHA-256, retrieved and available UTC timestamps,
and matching canonical `game_pk` and `player_mlbam_id`. Both paired-snapshot
timestamps must be at or before the historical row's `as_of_utc` and strictly
before scheduled first pitch.

## Feature families

Every ready family must retain the registered endpoint ID, canonical keys,
complete endpoint/request provenance, retrieved UTC timestamp, raw response
hash, source freshness and failure states, source-time availability relative to
the cutoff, source denominator definitions, and feature-construction version.
Missing, stale, malformed, retryable, terminal, or unprovenanced input blocks
the stated use; no season-average, display-name, projected-lineup, narrative,
or BvP substitution is allowed.

| Family | Authorized source endpoint and canonical keys | Availability and sample rule | Fail-closed rule |
| --- | --- | --- | --- |
| Historical labels | `mlb_statsapi_box_scores`; `game_pk`, `player_mlbam_id`, `game_date`, `season` | Final official result, paired with a preserved pregame feature snapshot | Missing labels or snapshots block training. |
| Lineup / PA | `mlb_statsapi_lineup_confirmation`; `game_pk`, `player_mlbam_id`, `game_date`; retain official batting-order slot. A future PA feature must have a separately versioned, pregame source contract. | Exactly nine canonical IDs in official slots 1–9; any PA input must be source-timed before cutoff. | Partial or nonofficial lineups, missing slot, or missing PA feature block inference and training examples. |
| Hitter baseline | `savant_expected_statistics`; `player_mlbam_id`, `season` | Retain xBA/xSLG/xwOBA, PA, BBE, source fields, and the stabilization decision. Use only source-backed, versioned feature construction. | Missing provenance, denominator, or stabilization/baseline rule blocks use. xBA is never a probability. |
| Starter handedness | `mlb_statsapi_probable_pitchers`; `game_pk`, `opponent_pitcher_mlbam_id`, `game_date` | Recorded probable/actual starter ID and throwing hand must be available before cutoff; actual fresh evidence outranks probable evidence. | Missing or unresolved hand blocks pregame inference. |
| Regressed platoon | `savant_custom_leaderboards`; `player_mlbam_id`, `season` | Preserve split definition, BIP and pitches-seen denominators, source query, and a separately versioned regression-to-baseline rule. The provisional floor is 50 BIP and 75 pitches seen. | Below-floor raw split cannot be used; a missing documented baseline/regression blocks use. |
| Arsenal fit | `savant_pitch_arsenal_batter`; `player_mlbam_id`, `season`, and pitch type/family | Preserve declared pitch-family mapping, pitches seen, BIP/BBE, source query, and stabilization decision. | Missing arsenal evidence is not replaced by BvP at any sample size. |
| Park | `savant_statcast_park_factors`; `venue_mlbam_id`, `season` | Preserve rolling years, event class, batter side, 100-is-average factor, request, and retrieval time. | Sutter Health Park is `UNVERIFIED`; never borrow a factor. Pre-2026 Tropicana factors are a `TROPICANA_PRE_2026_PARK_FACTOR_DISCONTINUITY` limitation and cannot be used without documented 2026 treatment. |
| Bearing-relative weather | `deferred_weather_interface`; `game_pk`, `venue_mlbam_id`, `game_date` | Only a provider with registered provenance may supply valid/retrieval times, venue identity, roof state, wind direction/speed, center-field bearing, and documented outward-wind normalization. | Weather remains `DEFERRED` while no provider is registered. If a weather adjustment is enabled, missing/unverified inputs block use; there is no weather multiplier or coefficient. |

Park and weather are disabled adjustments until a separately versioned model
specification enables them.  Disabled adjustments are reported as `DEFERRED`,
not silently treated as neutral data.

## Evaluation and audit requirements

Splits must be chronological and leakage-safe: train on completed games before
the validation interval, validate on the next contiguous completed interval,
and reserve a final later contiguous test interval.  No row from a later game,
later snapshot, or final-result field may appear in an earlier feature set.

Any future fitted model must report Brier score and calibration bins on
validation and held-out test data.  The prediction audit ledger must retain the
target version, model/feature version, as-of and cutoff timestamps, canonical
game/player IDs, every feature value or omission, source observations and raw
hashes, readiness blockers, prediction output only after this contract is
promoted, and the final official label.  This planning slice emits no fitted
model, probability, ranking, or player output.
