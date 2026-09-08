# Historical Snapshot Backfill v0

This is a local immutable-corpus manifest contract, not a crawler, HTTP client,
or live backfill service. A manifest is built only from an explicit JSON index
and explicit local snapshot root.

Each offered game names exactly three roles: `schedule`, `pregame_live_feed`,
and `final_box_score`. Their source endpoint IDs are respectively
`mlb_statsapi_schedule_by_date`, `mlb_statsapi_lineup_confirmation`, and
`mlb_statsapi_box_scores`. Every role retains source, endpoint URL, submitted
parameters, UTC retrieval time, raw SHA-256, source freshness/failure states,
canonical gamePk, role, and a path relative to the supplied snapshot root.
The pregame role additionally retains `available_at_utc`.

The pregame retrieval time and availability time must be at or before
`as_of_utc`, and `as_of_utc` must be strictly before scheduled first pitch. A
final box score must carry `outcome_only: true`; its retrieval may occur after
the game and is never subject to the pregame cutoff. It can label a player but
cannot create a lineup or starter fact.

Pregame lineup confirmation delegates to the canonical `normalize_game_lineups`
adapter. The authoritative arrays are
`liveData.boxscore.teams.away.battingOrder` and
`liveData.boxscore.teams.home.battingOrder`; their array positions establish
slots 1 through 9. Each side is `confirmed` only when normalization finds
exactly nine unique positive integer MLBAM IDs in those slots and every ID
matches `players.ID{id}.person.id`. Away and home are evaluated independently:
a complete away side is retained even when home is missing, empty, or partial.
If neither side is confirmed, the pregame role is rejected with
`PARTIAL_OR_MISSING_OFFICIAL_LINEUP`; no partial players are retained.
Retained pregame records include the canonical statuses
`away_lineup_status` and `home_lineup_status`, plus
`away_confirmed_hitter_count` and `home_confirmed_hitter_count`. Counts are
always 9 for `confirmed` and 0 for `unconfirmed`.

Manifest validation rejects `INVALID_GAME_PK`, `INVALID_GAME_DATE`,
`INVALID_TIMESTAMP`, `PREDICTION_CUTOFF_NOT_STRICTLY_PREGAME`,
`MISSING_SNAPSHOT_ROLE`, `INVALID_SNAPSHOT_ROLE`,
`DUPLICATE_CONFLICTING_ROLE_SNAPSHOT`, `DUPLICATE_GAME_ENTRY`,
`PATH_TRAVERSAL_OR_MISSING_SNAPSHOT`, `MALFORMED_SOURCE_EVIDENCE`,
`RAW_HASH_MISMATCH`, `UNVERIFIED_SOURCE_EVIDENCE`,
`STALE_SOURCE_EVIDENCE`, `RETRYABLE_SOURCE_EVIDENCE`,
`TERMINAL_SOURCE_EVIDENCE`, `MISMATCHED_GAME_PK`,
`PREGAME_SNAPSHOT_RETRIEVED_AFTER_AS_OF_UTC`,
`PREGAME_SNAPSHOT_AVAILABLE_AFTER_AS_OF_UTC`,
`PREGAME_SNAPSHOT_NOT_STRICTLY_PREGAME`, and
`FINAL_BOX_NOT_OUTCOME_ONLY`. A valid triplet whose canonical pregame feed has
no confirmed side is rejected with `PARTIAL_OR_MISSING_OFFICIAL_LINEUP`.
Malformed top-level indexes fail with `MALFORMED_INDEX` and malformed entries
with `MALFORMED_INDEX_ENTRY`. Rejections retain gamePk when known, status,
role, and a machine-readable reason.

Coverage reports include games offered, complete triplets, valid pregame
snapshots (retained records with at least one confirmed side), valid
ledger-eligible games (complete triplets with at least one confirmed side),
excluded games by reason, duplicate snapshot conflicts, and source-health
distribution. A one-confirmed-side game counts once. Deterministic order is by
gamePk then role (`schedule`, `pregame_live_feed`, `final_box_score`).

The manifest is evidence only. It contains no derived model features,
probabilities, rankings, recommendations, or network behavior.



