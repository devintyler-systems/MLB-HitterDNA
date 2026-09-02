# HitterDNA Source Endpoint Registry

Status: human-readable retrieval contract, 2026-09-01.

This document is the source of truth for how HitterDNA identifies, retrieves,
joins, refreshes, and fails closed on external source data. It does not itself
authorize a network integration. Endpoint facts and source capabilities below
are attributed in their table rows with the source name and retrieval date.

## 1. Governing retrieval rules

Every retrieved fact must retain all of the following:

- source name;
- exact endpoint or URL;
- request and query parameters exactly as submitted;
- retrieval timestamp as an RFC 3339 UTC timestamp;
- raw response hash;
- response row count where relevant;
- source freshness status;
- source failure status; and
- every canonical key required to join the fact downstream.

The required canonical keys, where applicable, are `game_pk`,
`player_mlbam_id`, `opponent_pitcher_mlbam_id`, `venue_mlbam_id`, `season`, and
`game_date`. Source-specific identifiers may be retained in addition to these
keys, but may not replace them.

Missing or invalid provenance makes the downstream datum `UNVERIFIED`. A value
without its original denominator, response type, and parameter set is not a
reproducible fact and must not be promoted.

Screenshots, unsourced aggregators, and narrative recaps are prohibited as
production inputs. They may create a discovery lead, but that lead must be
retrieved again from a registered source before it can enter a filter, model,
or market workflow.

Retrieval must be read-only. A registry entry describes evidence intake; it
does not define a model coefficient, probability conversion, recommendation,
or fallback value.

## 2. MLB Stats API registry

All MLB Stats API registry requests use `GET`. Source: MLB Stats API
interfaces; retrieved 2026-09-01. Persist the final request URL, all path and
query parameters, UTC retrieval time, response status, raw response hash, and
canonical keys. A response must be normalized by `game_pk`; a same-date team
pairing is never an event key.

| Registry item | Purpose | Endpoint template and required parameters | Response format | Expected canonical keys | Refresh timing | Source-of-truth priority and source basis |
| --- | --- | --- | --- | --- | --- | --- |
| Schedule by date | Establish every event on the slate, status, scheduled start, teams, probable pitchers, and venue. | `https://statsapi.mlb.com/api/v1/schedule`; required query: `sportId=1`, `date={game_date}`, `hydrate=probablePitcher,venue`. | JSON object containing date-scoped result arrays; preserve HTTP status, response headers where available, and raw response bytes before normalization. | `game_pk`, `game_date`, `venue_mlbam_id`, and probable-pitcher MLBAM IDs mapped to the appropriate home/away role. | Retrieve at slate creation; refresh before lineup intake and whenever an event status changes. | Primary for event identity and pregame schedule context. The live game feed supersedes schedule fields when it supplies newer game-specific state. Source: MLB Stats API schedule; retrieved 2026-09-01. |
| Game feed / live game details | Retrieve current game state, participants, probable-pitcher details, boxscore structures, and live game evidence. | `https://statsapi.mlb.com/api/v1.1/game/{game_pk}/feed/live`; required path parameter: positive `game_pk`; no query parameter is required by this contract. | JSON object keyed by game structure; preserve HTTP status, response headers where available, and raw response bytes before normalization. | `game_pk`, `game_date`, `player_mlbam_id`, `opponent_pitcher_mlbam_id`, `venue_mlbam_id`. | Refresh only for pregame-refresh-eligible games; increase urgency at warmup, then stop pregame refresh when the event is in progress or terminal. | Primary game-specific source when it is fresher than schedule data. Source: MLB Stats API live game feed; retrieved 2026-09-01. |
| Lineup confirmation | Establish an official nine-player batting order for each team; projected or inferred orders are not confirmation. | Use `https://statsapi.mlb.com/api/v1.1/game/{game_pk}/feed/live`; require `liveData.boxscore.teams.{away,home}.battingOrder` plus matching player records and positive player IDs. | JSON object keyed by game structure; preserve HTTP status, response headers where available, and raw response bytes before normalization. | `game_pk`, `game_date`, `player_mlbam_id`; retain team ID, batting-order slot, and position as descriptive fields. | Refresh for `eligible_refresh` and `urgent_refresh` games until each side has exactly nine unique positive player IDs in slots 1 through 9 or until the game becomes ineligible. | Sole production confirmation source. Previous lineups, projections, rosters, and postgame box scores cannot confirm a pregame lineup. Source: MLB Stats API live game feed; retrieved 2026-09-01. |
| Probable pitchers | Establish the currently supplied probable pitcher and throwing hand before an actual starter is known. | First use the schedule request above with `hydrate=probablePitcher,venue`; then use `https://statsapi.mlb.com/api/v1.1/game/{game_pk}/feed/live` and `gameData.probablePitchers`. | JSON object keyed by game structure; preserve HTTP status, response headers where available, and raw response bytes before normalization. | `game_pk`, `game_date`, `opponent_pitcher_mlbam_id`; retain pitcher name and throwing hand. | Refresh with schedule and eligible live-feed refreshes through the pregame window. | Actual starter evidence outranks live-feed probable data, which outranks schedule probable data. A stale probable must never overwrite an actual starter. Source: MLB Stats API schedule and live game feed; retrieved 2026-09-01. |
| Player identity | Resolve and verify MLBAM player identity without fuzzy or guessed joins. | Detail: `https://statsapi.mlb.com/api/v1/people/{player_mlbam_id}` with a positive path ID. Authoritative season list for exact normalized-name ingress: `https://statsapi.mlb.com/api/v1/sports/1/players` with required query `season={season}`. | JSON object; preserve HTTP status, response headers where available, and raw response bytes before normalization. | `player_mlbam_id`, `season`; add `game_pk` and `game_date` only when joined to an event. | Resolve on first ingress; refresh after an unresolved or conflicting identity, or when source identity fields materially change. | MLBAM ID is authoritative. Resolve a name only when one exact normalized match has one positive ID; zero matches are unresolved and multiple matches are ambiguous. Source: MLB Stats API people and sport-player interfaces; retrieved 2026-09-01. |
| Venue identity | Resolve the official venue ID and identity fields used by event and park joins. | `https://statsapi.mlb.com/api/v1/venues/{venue_mlbam_id}`; required path parameter: positive `venue_mlbam_id`. | JSON object; preserve HTTP status, response headers where available, and raw response bytes before normalization. | `venue_mlbam_id`; add `game_pk`, `game_date`, and `season` when joined to an event or park factor. | Retrieve on a previously unseen venue ID and refresh when schedule/feed venue identity conflicts with stored metadata. | Schedule/feed venue ID identifies the event venue; venue detail supplies identity metadata and may not silently redirect a game to another park. Source: MLB Stats API venue interface; retrieved 2026-09-01. |
| Transactions / IL context | Record official transactions relevant to availability and injured-list context without treating them as lineup confirmation. | `https://statsapi.mlb.com/api/v1/transactions`; required query: `sportId=1`, `startDate={start_date}`, `endDate={end_date}`; use `playerId={player_mlbam_id}` or `teamId={team_id}` when the request is scoped, and retain every submitted parameter. | JSON object containing date-scoped result arrays; preserve HTTP status, response headers where available, and raw response bytes before normalization. | `player_mlbam_id`, `game_date`, `season`; retain team ID and transaction identifier. | Retrieve for the slate date and refresh when an official transaction changes player availability. | Official transaction context may exclude or hold a player, but only the live feed can confirm the batting order. Source: MLB Stats API transactions interface; retrieved 2026-09-01. |
| Box scores | Verify actual participants, starters, and completed-game results; never backfill pregame confirmation. | `https://statsapi.mlb.com/api/v1/game/{game_pk}/boxscore`; required path parameter: positive `game_pk`. | JSON object keyed by game structure; preserve HTTP status, response headers where available, and raw response bytes before normalization. | `game_pk`, `game_date`, `player_mlbam_id`, `opponent_pitcher_mlbam_id`, `venue_mlbam_id`. | Retrieve after actual participation appears and refresh through finalization or official correction. | Primary for actual game participation and boxscore results. It may correct a stale probable pitcher but cannot retroactively create a pregame confirmed-lineup observation. Source: MLB Stats API boxscore interface; retrieved 2026-09-01. |

For every MLB Stats API entry, a non-2xx response, malformed JSON, missing required canonical key, or schema-incompatible payload records `source_failure_status=RETRYABLE` only when the failure is transient; otherwise record `TERMINAL` and emit `UNVERIFIED`. A response past its entry TTL records `source_freshness_status=STALE`, remains auditable, and cannot satisfy lineup confirmation or override a fresher higher-priority observation.

The schedule and live-feed endpoint patterns above are already represented by
the repository adapters. The remaining patterns are registry contracts, not an
instruction to add clients in this branch. Source: HitterDNA Stats API and
lineup adapters; retrieved 2026-09-01.

## 3. Baseball Savant registry

Every Savant retrieval must preserve the exact export URL, complete filters,
response format, UTC retrieval timestamp, raw response hash, row count,
requested season/date window, and all player or venue identifiers present in
the response. Presentation labels are not canonical identities; join on MLBAM
IDs or an explicitly resolved venue ID.

Where a reviewed direct export URL is not registered below, retrieval is
non-automated: an operator must export from the named Baseball Savant interface
and retain the exact generated request URL and parameters. An undocumented
route must not be guessed from a page name.

| Registry item | Purpose | Required filters / retrieval contract | Output identifiers and metadata | Known limitations and source basis |
| --- | --- | --- | --- | --- |
| Expected Statistics leaderboard | Supply season batter xBA, xSLG, xwOBA, PA, and batted-ball-event fields as raw observations. | Direct CSV: `https://baseballsavant.mlb.com/leaderboard/expected_statistics?type=batter&year={season}&csv=true`; require batter view, one `season`, and CSV response. | `player_mlbam_id`, `season`; retain source URL, requested season, row count, UTC retrieval timestamp, and response hash. | Missing IDs remain unresolved; duplicate valid MLBAM IDs or incompatible requested/returned seasons are malformed. Metrics remain unverified until the stabilization layer evaluates them. Source: Baseball Savant Expected Statistics leaderboard; retrieved 2026-09-01. |
| Custom Leaderboards | Retrieve explicitly selected batter or pitcher metrics and splits that are unavailable from the fixed expected-statistics contract. | Non-automated until a reviewed export route is registered. Require season/date scope, batter-or-pitcher view, metric list, minimum denominator, split/handedness filters, and the exact exported request parameters. | `player_mlbam_id`, `season`; include `game_date` for date-bounded exports and retain metric names plus their denominator fields. | Custom layouts can emit different columns and denominators. Treat each saved response contract as versioned; do not merge it merely because display labels match. Source: Baseball Savant Custom Leaderboards; retrieved 2026-09-01. |
| Pitch Arsenal Stats, batter view | Describe batter results and exposure by pitch type or declared pitch family for matchup analysis. | Non-automated until a reviewed export route is registered. Require batter view, season/date scope, pitch type or documented pitch-family mapping, batter side or pitcher hand when used, and minimum pitches/BIP filters. | `player_mlbam_id`, `season`; retain pitch type/family, pitches seen, BIP/BBE, and any returned xwOBA/xSLG fields with their source denominators. | Pitch exposure, BIP/BBE, and PA are not interchangeable. A pitch-family mapping must be versioned and retained with the request. Source: Baseball Savant Pitch Arsenal Stats, batter view; retrieved 2026-09-01. |
| Statcast Search CSV | Supply reproducible pitch-level or event-level extracts for declared date windows and player scopes. | Base CSV interface: `https://baseballsavant.mlb.com/statcast_search/csv`; require response type, batter/pitcher role, start date, end date, season/game-type scope, and batter/pitcher IDs when player-scoped; retain every additional submitted filter. | `game_pk`, `game_date`, batter `player_mlbam_id`, `opponent_pitcher_mlbam_id`, and `season`; retain pitch/event identifiers where returned, query parameters, row count, UTC retrieval timestamp, and response hash. | The response has a 30,000-row cap. Each extraction chunk must span five days or fewer. Response types with incompatible denominator definitions must never be silently combined. Source: Baseball Savant Statcast Search CSV; retrieved 2026-09-01. |
| Statcast Park Factors | Supply the park effect by event class and batter handedness while preserving the source index. | Non-automated until a reviewed export route is registered. Require `season`, three-year rolling window, event type, batter side, and venue selection; retain the exact export request. | `venue_mlbam_id`, `season`; retain rolling years, event, batter side, factor value, request parameters, UTC retrieval timestamp, and response hash. | Preserve the 100-is-average index. Sutter Health Park is `UNVERIFIED`; Rays factors crossing the 2025 outdoor season retain `rays_continuity_suspect`. Source: Baseball Savant Statcast Park Factors; retrieved 2026-09-01. |
| Bat Tracking | Supply source-defined bat speed, swing length, squared-up, blast, and related bat-tracking observations. | Non-automated until a reviewed export route is registered. Require season/date scope, batter scope, metric selection, and source denominator/minimum-swings filters; retain the exact export request. | `player_mlbam_id`, `season`, and `game_date` when available; retain swings or other source denominator, row count, UTC retrieval timestamp, and response hash. | Bat-tracking data begins in the second half of 2023; do not construct multiyear trends. Squared-up rate remains descriptive pending calibration. Source: Baseball Savant Bat Tracking; retrieved 2026-09-01. |
| Swing Path / Attack Angle | Supply source-defined attack angle, swing-path tilt, attack direction, and related swing-shape observations. | Non-automated until a reviewed export route is registered. Require season/date scope, batter scope, selected swing-path fields, and the source denominator/minimum-swings filters; retain the exact export request. | `player_mlbam_id`, `season`, and `game_date` when available; retain denominator, row count, UTC retrieval timestamp, and response hash. | Availability inherits the second-half-of-2023 bat-tracking boundary; do not construct multiyear trends or infer missing seasons. Squared-up rate remains descriptive pending calibration. Source: Baseball Savant Swing Path / Attack Angle and Bat Tracking; retrieved 2026-09-01. |

### Statcast Search chunk contract

The 30,000-row source cap is a hard ceiling, not a pagination mechanism.
Source: Baseball Savant Statcast Search CSV; retrieved 2026-09-01.

HitterDNA extraction policy therefore requires date-range requests in chunks
of five calendar days or fewer. Every chunk must store:

- inclusive start date and end date;
- batter and pitcher MLBAM IDs where relevant;
- the complete query-parameter set;
- returned row count; and
- raw response hash.

A chunk at the cap is incomplete and must be subdivided before promotion. Each
sub-chunk remains an independent provenance record. Combining chunks requires
compatible column contracts, event taxonomies, response types, and denominator
definitions. PA, AB, BF, pitches, swings, BIP, and BBE must never be silently
substituted for one another.

## 4. FanGraphs projection registry

No FanGraphs endpoint is registered by this document. Each entry below is
non-automated until HitterDNA reviews and authorizes a reproducible access or
export method. Manual retrieval must retain the original export, exact page or
download URL, selected projection date/season and filters, UTC retrieval time,
raw response hash, row count, and player identifiers used for the MLBAM join.

| Source | Permitted HitterDNA role | Required retrieval context | Automation status and source basis |
| --- | --- | --- | --- |
| THE BAT X | Batter projection baseline and context, including playing-time-dependent rate or counting projections only when their source columns and projection date are retained. | Projection system=`THE BAT X`, projection date, season, selected columns, player identifier, row count, and original export provenance. | Non-automated; no endpoint claim. Source: FanGraphs THE BAT X projections; retrieved 2026-09-01. |
| Steamer | Independent batter projection baseline or comparison source, never an unlabeled substitute for another system. | Projection system=`Steamer`, projection date, season, selected columns, player identifier, row count, and original export provenance. | Non-automated; no endpoint claim. Source: FanGraphs Steamer projections; retrieved 2026-09-01. |
| ATC | Independent consensus-style projection baseline or context, retained under its own system label. | Projection system=`ATC`, projection date, season, selected columns, player identifier, row count, and original export provenance. | Non-automated; no endpoint claim. Source: FanGraphs ATC projections; retrieved 2026-09-01. |
| Depth Charts | Playing-time and projection context retained as the `Depth Charts` system, not silently merged with Steamer, ATC, or THE BAT X. | Projection system=`Depth Charts`, projection date, season, selected columns, player identifier, row count, and original export provenance. | Non-automated; no endpoint claim. Source: FanGraphs Depth Charts projections; retrieved 2026-09-01. |
| RosterResource | Roster, role, depth-chart, and availability context; not official lineup confirmation and not a replacement for MLB transaction evidence. | Snapshot date/time, team and player identifiers, selected context fields, page/export URL, row count, and original response/export hash. | Non-automated; no endpoint claim. Source: FanGraphs RosterResource; retrieved 2026-09-01. |

Projection systems must remain separately labeled. HitterDNA must not average,
blend, or select among systems without a separately versioned modeling rule.
RosterResource context cannot change a player to `confirmed`; official lineup
confirmation remains an MLB Stats API live-feed fact. Source: FanGraphs
RosterResource and MLB Stats API live game feed; retrieved 2026-09-01.

## 5. Park/weather registry

The governing park and weather rules are in
[`docs/park-weather.md`](park-weather.md). Savant Statcast Park Factors supply
park effects under the season-window, event, handedness, venue, and exception
contract above. Source: Baseball Savant Statcast Park Factors; retrieved
2026-09-01.

The weather source remains provider-neutral and deferred. No provider endpoint
is registered, no API key or credential is defined, and no live weather client
is authorized here. A future provider must satisfy the park/weather provenance
contract for game and venue identity, valid/retrieval timestamps, request,
provider location key, and raw response hash before its observation is usable.

Weather normalization may compute only the documented bearing-relative wind
component. This registry defines no weather probability multiplier, park
adjustment weight, or model coefficient. Closed or unknown roofs and incomplete
bearing/provenance inputs remain explicitly omitted under the referenced
contract. Source: HitterDNA Park and Weather Contract; retrieved 2026-09-01.

## 6. Deferred market registry

Market data is a future interface only. A future market observation must retain:

- source;
- book;
- market identifier;
- timestamp;
- raw odds;
- normalized event and player IDs;
- devig method; and
- provenance.

Market provenance must retain the source endpoint or URL, exact request/query parameters, UTC retrieval timestamp, raw response hash, response row count where relevant, source freshness status, and source failure status.

Normalized IDs must include `game_pk` and `player_mlbam_id` where the market is
player-specific. The interface must also preserve the raw provider event and
participant identifiers so normalization can be audited.

No market endpoint, credential, current price, price estimate, implied
probability, fair probability, or edge is defined by this registry. Missing
book, timestamp, odds, identifier, or devig provenance leaves any downstream
market comparison `UNVERIFIED`.

## 7. Failure and freshness policy

Outcome state and source-retrieval state are separate audit dimensions. A
retryable retrieval can still leave a datum `UNVERIFIED`; a terminal event can
produce a verified `FAIL`.

`source_freshness_status` is one of `PASS`, `STALE`, or `UNVERIFIED`. `source_failure_status` is one of `PASS`, `RETRYABLE`, `TERMINAL`, `FAIL`, or `UNVERIFIED`. The two fields must be retained independently.

| State | Contract meaning | Required action |
| --- | --- | --- |
| `PASS` | A fresh, authoritative, fully sourced observation satisfies the registered response and join contract. | Retain the value and full provenance; it is eligible for downstream use subject to the consuming contract. |
| `FAIL` | An authoritative, fresh, fully sourced observation exists and proves that a required condition is not satisfied. | Preserve the value and provenance, record the failed rule, and block advancement. Do not retry merely to seek a different answer. |
| `UNVERIFIED` | Required evidence, provenance, canonical keys, denominator, response contract, or safe join is missing, malformed, ambiguous, or stale. | Block advancement. Retry only when the underlying source state is retryable; otherwise require source or contract repair. |
| `STALE` | The observation exceeds its registered freshness TTL or predates a higher-priority authoritative state change. | Retain for audit but prohibit it from confirming or overriding current state. Refresh from the registered source or remain `UNVERIFIED`. |
| `RETRYABLE` | Retrieval failed for a transient condition and the same registered request can be attempted again without changing its meaning. | Record the attempt, error class, timestamp, and request provenance; retry under bounded orchestration and never fabricate a value between attempts. |
| `TERMINAL` | The source or event is in a known non-retriable state for the current analysis, such as an authoritative terminal game state, invalid canonical identity, or response contract that cannot be safely parsed. | Stop automatic retries for that analysis, retain the terminal reason, and emit `FAIL` or `UNVERIFIED` according to whether the source proved a condition false or failed to establish it. |

A stale lineup can never become `confirmed`. A stale probable pitcher can never
override an actual starter. When sources disagree, choose the fresh,
higher-priority authoritative source and retain the conflict; do not erase the
older observation.

No missing source input may be filled with a season average, previous lineup,
projected lineup, inferred roster role, narrative recap, or other narrative
substitute. Baseline or shrinkage behavior is allowed only when a separate,
versioned model contract explicitly requests a source-backed baseline; it does
not repair missing source evidence. Every normalized or derived join record
must retain the source name, endpoint or URL, request parameters, retrieval
timestamp, raw response hash, source freshness status, and source failure
status of every contributing source observation.

## 8. Machine-readable follow-on

A future machine-readable endpoint registry must contain exactly these required
fields for every entry:

```text
endpoint_id
source
domain
purpose
endpoint_template
method
parameter_contract
response_format
canonical_keys
retrieval_cadence
freshness_ttl_minutes
pagination_contract
row_limit
chunking_contract
provenance_fields
exception_policy
implementation_status
```
