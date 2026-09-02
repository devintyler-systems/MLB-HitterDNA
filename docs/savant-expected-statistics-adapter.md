# Baseball Savant Expected Statistics Adapter

## Source and retrieval

The adapter retrieves the public Baseball Savant Expected Statistics
leaderboard as a direct CSV response, not rendered HTML:

`https://baseballsavant.mlb.com/leaderboard/expected_statistics?type=batter&year={season}&csv=true`

The requested season is retained verbatim in `source_url`. Every normalized row
also carries that URL and the UTC retrieval timestamp. Network or non-200
failures are `unavailable`; invalid or structurally unusable payloads are
`malformed`.

## Fields and aliases

The preferred CSV aliases are `player_id`, `player_name`, `year`, `pa`, `bbe`,
`ba`, `est_ba`, `slg`, `est_slg`, `woba`, and `est_woba`. The parser also
accepts documented presentation aliases such as `playerId`, `season`, `bip`,
`xba`, `xslg`, and `xwoba`, case-insensitively. It accepts JSON collections
named `data`, `rows`, or `results` when supplied directly to the pure parser.

Blank or null-like numeric cells normalize to `None`; other invalid numeric
cells make the payload malformed. Rows without a player name or valid season
are rejected. A missing or invalid player ID remains `None`; this adapter never
resolves players by name. Duplicate valid MLBAM IDs make the payload malformed.

## MetricObservation mapping

| Metric key | Source field | Sample type | Sample size |
| --- | --- | --- | --- |
| `expected_batting_average` | `est_ba` / xBA | `pa` | PA |
| `expected_slugging` | `est_slg` / xSLG | `pa` | PA |
| `expected_woba` | `est_woba` / xwOBA | `pa` | PA |
| `plate_appearances` | `pa` | `pa` | PA |
| `batted_ball_events` | `bbe` / BIP | `bip` | BBE |

Every observation uses the exact source name `Baseball Savant Expected
Statistics`, preserves raw source data, and has `stabilization_status` set to
`unverified`. Stabilization is deliberately deferred to the stabilization
layer; this adapter contains no threshold or qualification judgment.

## Scope boundary

This is a read-only raw-metric adapter. It does not evaluate filters, set
thresholds, calculate models or probabilities, use markets, or create any
recommendation. It does not use BvP, streaks, last-seven statistics, droughts,
or box-score recency.
