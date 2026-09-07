# Historical Pregame Training Ledger v0

Status: local captured-data ledger only. This contract creates no fitted model,
score, rank, probability, or recommendation.

## Row and target

There is at most one included row per canonical player-game: `game_pk` plus
`player_mlbam_id`. The sole target is `official_hit_ge_1`, a boolean derived
only from the final official MLB Stats API box score's batting `hits` field for
that player-game. A final box score is outcome-only evidence; it cannot
retroactively confirm a pregame lineup, batting-order slot, starter, or feature.

Each row retains canonical `game_pk`, `game_date`, `season`, hitter MLBAM ID,
team MLBAM ID, opponent team MLBAM ID, venue MLBAM ID, and opponent starter
MLBAM ID when pregame evidence supplies one. Display names are never join keys.

## Historical cutoff and evidence

Every historical row has an `as_of_utc` and `scheduled_first_pitch_utc`, with
`as_of_utc < scheduled_first_pitch_utc`. A paired pregame live-feed snapshot
must be retrieved and available at or before `as_of_utc`, and strictly before
first pitch. Every pregame feature/snapshot follows that same retrieval and
availability rule.

Final box-score provenance is separate label evidence and may be retrieved
after game completion. It retains endpoint ID/URL, submitted parameters,
retrieval UTC timestamp, raw SHA-256, canonical keys, and source health. The
paired pregame snapshot separately retains the same provenance plus its
availability timestamp. Neither provenance record may overwrite the other.

Lineup confirmation requires exactly nine unique positive canonical MLBAM IDs
from `liveData.boxscore.teams.{away,home}.battingOrder`, with matching
`players.ID{id}.person.id` records and official slots 1 through 9. A partial
side contributes no rows.

Starter evidence uses a pregame actual starter only when its availability is at
or before `as_of_utc` and strictly before first pitch. Otherwise retain valid
pregame probable-starter evidence. A postgame actual starter is never backfilled
into a row and records `POSTGAME_ACTUAL_STARTER_BACKFILL` as an exclusion.

## Deterministic inclusion and rejection

Include only when schedule, pregame snapshot, and final box score agree on the
canonical game; the side has a complete official lineup; the hitter has a valid
official slot; the final box score supplies that hitter's official label; source
health is PASS; and all pregame timing rules pass.

Fail closed with: `MISSING_PREGAME_SNAPSHOT`, `MISMATCHED_GAME_PK`,
`INVALID_AS_OF_UTC`, `PREDICTION_CUTOFF_NOT_STRICTLY_PREGAME`,
`PREGAME_SNAPSHOT_RETRIEVED_AFTER_AS_OF_UTC`,
`PREGAME_SNAPSHOT_AVAILABLE_AFTER_AS_OF_UTC`,
`PREGAME_SNAPSHOT_NOT_STRICTLY_PREGAME`,
`PARTIAL_OR_MISSING_OFFICIAL_LINEUP`, `CANONICAL_PLAYER_ID_MISMATCH`,
`ABSENT_FINAL_OFFICIAL_LABEL`, `FINAL_HITTER_NOT_IN_PREGAME_CONFIRMED_LINEUP`,
`MALFORMED_SOURCE_EVIDENCE`, `STALE_SOURCE_EVIDENCE`, `RETRYABLE_SOURCE_EVIDENCE`,
`TERMINAL_SOURCE_EVIDENCE`, and `POSTGAME_ACTUAL_STARTER_BACKFILL`.

Rows sort by `game_pk`, team side, official batting-order slot, then hitter
MLBAM ID. The artifact may record chronological partition metadata (time order
and partition labels) for a future dataset, but it does not create a model split
or model.
