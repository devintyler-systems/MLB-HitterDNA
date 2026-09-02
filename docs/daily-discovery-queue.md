# Daily Discovery Queue

## Purpose

The Daily Discovery Queue is the mandatory research gate between external alerts and the HitterDNA candidate filter table.

No alert, screenshot, manual note, batter-versus-pitcher split, composite score, market movement, or third-party dashboard signal may enter a recommendation directly.

## Flow

schedule -> freshness gate -> confirmed-lineup ingestion -> candidate filter table -> model -> market evaluation

## Identity-resolution ingress

An intake record may use `null` for `game_pk` and `player_mlbam_id` while resolution is pending. Resolve it in this order:

1. Query the MLB Stats API schedule for the analysis date.
2. Match one exact away/home team pairing and store its authoritative `gamePk`, venue, probable pitchers, and game status.
3. Resolve the candidate name against an authoritative player list only when exactly one normalized name match has a positive MLBAM ID.
4. Mark zero matches as `unresolved` and multiple matches as `ambiguous`; do not infer, reuse, or invent an ID.
5. Allow the Python queue gate to advance only records with positive `game_pk` and `player_mlbam_id` values.

`null`, zero, missing, malformed, unresolved, and ambiguous identifiers remain outside the candidate filter table.

## Schedule freshness gate

`game_pk` is the event key: retain every scheduled event by that key, including
same-date doubleheaders with the same team pairing. Only `eligible_refresh` and
`urgent_refresh` games can proceed to the lineup-refresh stage. Every exclusion
or hold status is blocked from pregame candidate generation.

## Confirmed-lineup ingestion

The MLB Stats API live game feed is the authoritative lineup source. Each
lineup record retains the feed URL and retrieval timestamp. A player may enter
candidate filtering only when the feed establishes a `confirmed` nine-player
batting order; `UNCONFIRMED` is an explicit exclusion state, never a forecast.
Do not use prior lineups, projected lineups, roster inference, box scores, or
third-party lineup sources as confirmation. `game_pk` remains the sole event
key, so no team/date deduplication is permitted.

## Permitted sources

- Baseball Savant
- MLB Stats API
- FanGraphs daily projections
- Statcast park factors
- Verified weather source
- Market feed
- Approved third-party discovery tools, including Barrellab, only after raw-feature decomposition

## Prohibited evidence

- Hit streaks
- Last-seven or last-night results
- Drought logic
- Batter-versus-pitcher evidence below 50 PA
- Opaque composite scores without raw components
- Unsourced screenshots
- Market-price recollection or estimates

## BvP policy

BvP below 50 PA is stored only as: DESCRIPTIVE ONLY, NOT PREDICTIVE.

It receives zero model weight. The replacement is hitter performance against the opposing pitcher's projected arsenal, velocity bands, handedness, and locations.

## Advancement rules

A record advances from INVESTIGATE to ADVANCE only when:

1. The player MLBAM ID and gamePk resolve.
2. The source URL and retrieval timestamp exist.
3. The sample type and sample size exist.
4. The feature maps to a defined model feature.
5. Stabilization status is PASS or the model explicitly permits a shrinkage treatment.
6. The hitter has a CONFIRMED lineup status.
7. The candidate does not duplicate an existing feature without an explicit correlation treatment.

A record advances from ADVANCE to PRICED only when:

1. Book, line, price, and timestamp exist.
2. The fair probability is computed through the analysis tool.
3. Devig method and devigged market probability are stored.
4. Edge is stored in percentage points.

## Required dispositions

- INVESTIGATE: valid lead awaiting source or feature validation
- ADVANCE: validated feature eligible for filter table
- HOLD: valid but awaiting lineup, weather, or price
- DROP: invalid, prohibited, duplicate, or inadequate sample
- ARCHIVED: completed slate record
