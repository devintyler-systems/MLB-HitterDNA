"""Read-only collection and attachment of authorized Live Slate source inputs."""

from __future__ import annotations

import argparse
from dataclasses import dataclass, replace
from datetime import date as calendar_date
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping, Protocol, Sequence

import requests

from hitterdna.lineups import GAME_FEED_URL, normalize_game_lineups
from hitterdna.prepare_slate_fixture import _validate_statsapi_schedule
from hitterdna.run_slate import (
    CONTRIBUTING_ENDPOINT_IDS,
    SlateRunValidationError,
    _computed_status,
    _provenance_health,
    _validate_provenance,
)
from hitterdna.savant_expected_stats import expected_stats_source_url, parse_expected_stats_payload
from hitterdna.source_endpoint_registry import load_source_endpoint_registry
from hitterdna.statsapi import GameContext, normalize_schedule_contexts


SCHEDULE_ENDPOINT_ID = "mlb_statsapi_schedule_by_date"
LINEUP_ENDPOINT_ID = "mlb_statsapi_lineup_confirmation"
PROBABLE_ENDPOINT_ID = "mlb_statsapi_probable_pitchers"
SAVANT_ENDPOINT_ID = "savant_expected_statistics"
_HTTP_TIMEOUT_SECONDS = 10.0


class SlateCollectionError(ValueError):
    """A local fixture or collection request cannot be safely processed."""


class HttpResponse(Protocol):
    status_code: int
    content: bytes


class HttpTransport(Protocol):
    """The single injectable seam for all collection HTTP reads."""

    def get(self, url: str, *, params: Mapping[str, str | int] | None, timeout: float) -> HttpResponse: ...


@dataclass(frozen=True)
class CollectedResponse:
    endpoint_id: str
    endpoint_or_url: str
    submitted_parameters: dict[str, str | int]
    retrieved_at_utc: str
    source_freshness_status: str
    source_failure_status: str
    raw_bytes: bytes | None
    raw_response_hash: str | None
    snapshot_path: Path | None
    reason: str | None
    fetch_status: str = "UNVERIFIED"
    parser_status: str = "NOT_ATTEMPTED"
    attachment_status: str = "UNVERIFIED"
    lineup_side_statuses: Mapping[str, str] | None = None

    @property
    def status(self) -> str:
        return _computed_status(self.source_freshness_status, self.source_failure_status)


def collect_slate_inputs(
    game_date: str,
    fixture_path: Path,
    snapshot_dir: Path,
    *,
    transport: HttpTransport | None = None,
    retrieved_at_utc: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Collect only registry-authorized snapshots and atomically update a fixture."""

    timestamp = retrieved_at_utc or _utc_now()
    fixture = _load_and_validate_fixture(fixture_path, game_date)
    entries = {entry.endpoint_id: entry for entry in load_source_endpoint_registry()}
    client: HttpTransport = transport or requests.Session()
    snapshot_dir = snapshot_dir.resolve()
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    reports: list[dict[str, Any]] = []

    contexts = _healthy_contexts(fixture)
    if contexts is None:
        schedule = _collect_schedule(game_date, snapshot_dir, client, entries, timestamp)
        reports.append(_report_row(schedule))
        if schedule.status == "PASS" and schedule.raw_bytes is not None:
            try:
                parsed_schedule = json.loads(schedule.raw_bytes.decode("utf-8"))
                if not isinstance(parsed_schedule, Mapping):
                    raise SlateCollectionError("schedule response must be an object")
                games = _validate_statsapi_schedule(parsed_schedule, game_date)
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
                schedule = _terminal(schedule, f"schedule response is malformed: {error}")
                reports[-1] = _report_row(schedule)
                _leave_unverified(fixture, SCHEDULE_ENDPOINT_ID, schedule.reason or "schedule is unavailable")
                contexts = None
            else:
                fixture["schedule"] = parsed_schedule
                _set_healthy_source(fixture, SCHEDULE_ENDPOINT_ID, _provenance(
                    schedule, "MLB Stats API", len(games), {"game_pk": [game["gamePk"] for game in games]},
                ))
                contexts = tuple(normalize_schedule_contexts(dict(parsed_schedule)))
        else:
            _leave_unverified(fixture, SCHEDULE_ENDPOINT_ID, schedule.reason or "schedule is unavailable")
    else:
        reports.append({"endpoint_id": SCHEDULE_ENDPOINT_ID, "status": "PASS", "action": "reused_healthy_fixture", "snapshots": []})

    if contexts:
        _attach_schedule_probables(fixture, contexts)
        feed_results = [
            _validate_live_feed_response(contexts, response)
            for response in _collect_live_feeds(contexts, snapshot_dir, client, entries, timestamp)
        ]
        reports.extend(_report_row(result) for result in feed_results)
        _attach_live_feed_inputs(fixture, fixture_path.parent.resolve(), contexts, feed_results)
    else:
        _leave_unverified(fixture, LINEUP_ENDPOINT_ID, "healthy schedule context is unavailable")
        _leave_unverified(fixture, PROBABLE_ENDPOINT_ID, "healthy schedule context is unavailable")

    savant = _validate_savant_response(
        game_date, _collect_savant(game_date, snapshot_dir, client, entries, timestamp),
    )
    reports.append(_report_row(savant))
    _attach_savant_input(fixture, game_date, savant)
    _finalize_unavailable_inputs(fixture)
    fixture["retrieved_at_utc"] = timestamp

    manifest = {
        "artifact_type": "collection_manifest",
        "schema_version": "live-slate-input-collection-v0",
        "game_date": game_date,
        "generated_at_utc": timestamp,
        "fixture_path": str(fixture_path.resolve()),
        "snapshot_dir": str(snapshot_dir),
        "sources": reports,
    }
    report = {
        "artifact_type": "collection_report",
        "schema_version": "live-slate-input-collection-v0",
        "game_date": game_date,
        "generated_at_utc": timestamp,
        "status": _computed_status(*(str(row["status"]) for row in reports)),
        "sources": reports,
    }
    _atomic_write_json(fixture_path, fixture)
    _atomic_write_json(snapshot_dir / "collection_manifest.json", manifest)
    _atomic_write_json(snapshot_dir / "collection_report.json", report)
    return manifest, report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Collect authorized HitterDNA live slate input snapshots")
    parser.add_argument("--date", required=True, type=_iso_date, help="Slate date (YYYY-MM-DD)")
    parser.add_argument("--fixture", required=True, type=Path, help="Existing local Slate Run fixture")
    parser.add_argument("--snapshot-dir", required=True, type=Path, help="Immutable raw-response snapshot directory")
    args = parser.parse_args(argv)
    try:
        _, report = collect_slate_inputs(args.date, args.fixture, args.snapshot_dir)
    except (OSError, ValueError, SlateRunValidationError) as error:
        parser.error(str(error))
    return 0 if report["status"] not in {"TERMINAL"} else 1


def _load_and_validate_fixture(fixture_path: Path, game_date: str) -> dict[str, Any]:
    try:
        payload = json.loads(fixture_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SlateCollectionError(f"fixture is unreadable: {error}") from error
    if not isinstance(payload, dict) or payload.get("game_date") != game_date:
        raise SlateCollectionError("fixture game_date does not match --date")
    _migrate_legacy_lineup_hash_attachment(payload)
    _preflight_existing_savant_attachment(payload, game_date)
    entries = {entry.endpoint_id: entry for entry in load_source_endpoint_registry()}
    _validate_provenance(payload.get("provenance"), payload.get("unavailable_source_inputs"), entries)
    if not isinstance(payload.get("schedule"), Mapping):
        raise SlateCollectionError("fixture schedule is missing")
    return payload


def _migrate_legacy_lineup_hash_attachment(fixture: dict[str, Any]) -> None:
    """Detach the old JSON-in-hash aggregate so it can be recollected safely."""

    provenance = _mapping(fixture.get("provenance"))
    lineup = _mapping(provenance.get(LINEUP_ENDPOINT_ID))
    raw_hash = lineup.get("raw_response_hash")
    if not isinstance(raw_hash, str) or not raw_hash.lstrip().startswith("{"):
        return
    try:
        mapping = json.loads(raw_hash)
    except json.JSONDecodeError:
        mapping = None
    if not isinstance(mapping, Mapping) or not mapping:
        return
    fixture.setdefault("provenance", {}).pop(LINEUP_ENDPOINT_ID, None)
    fixture.setdefault("unavailable_source_inputs", {})[LINEUP_ENDPOINT_ID] = {
        "status": "UNVERIFIED",
        "reason": "legacy aggregated lineup hash was detached; recollection is required",
    }


def _preflight_existing_savant_attachment(fixture: dict[str, Any], game_date: str) -> None:
    """Prevent a previously fetch-only PASS attachment from reaching run_slate."""

    provenance = _mapping(fixture.get("provenance"))
    source = _mapping(provenance.get(SAVANT_ENDPOINT_ID))
    expected = _mapping(fixture.get("expected_statistics"))
    if not source or _provenance_health(source) != "PASS" or not expected:
        return
    try:
        parsed = parse_expected_stats_payload(
            int(game_date[:4]), expected.get("payload", ""), str(expected.get("source_url", "")),
            str(source.get("retrieved_at_utc", "")),
        )
    except (TypeError, UnicodeDecodeError, ValueError) as error:
        _mark_unusable_source(fixture, SAVANT_ENDPOINT_ID, f"existing attachment parser validation failed: {error}")
        return
    if parsed.fetch_status != "fetched":
        _mark_unusable_source(
            fixture, SAVANT_ENDPOINT_ID,
            f"existing attachment parser validation failed: {parsed.error_message or 'response is malformed'}",
        )


def _healthy_contexts(fixture: Mapping[str, Any]) -> tuple[GameContext, ...] | None:
    source = fixture["provenance"].get(SCHEDULE_ENDPOINT_ID)
    if not isinstance(source, Mapping) or _provenance_health(source) != "PASS":
        return None
    contexts = tuple(normalize_schedule_contexts(dict(fixture["schedule"])))
    if not contexts or any(context.game_pk is None for context in contexts):
        raise SlateCollectionError("fixture schedule has no canonical gamePk values")
    return contexts


def _collect_schedule(game_date: str, snapshot_dir: Path, transport: HttpTransport, entries: Mapping[str, Any], timestamp: str) -> CollectedResponse:
    entry = _authorized(entries, SCHEDULE_ENDPOINT_ID)
    return _fetch(
        SCHEDULE_ENDPOINT_ID, entry.endpoint_template, {"sportId": 1, "date": game_date, "hydrate": "probablePitcher,venue"},
        snapshot_dir, f"schedule-{game_date}", transport, timestamp,
    )


def _collect_live_feeds(contexts: Sequence[GameContext], snapshot_dir: Path, transport: HttpTransport, entries: Mapping[str, Any], timestamp: str) -> list[CollectedResponse]:
    _authorized(entries, LINEUP_ENDPOINT_ID)
    return [
        _fetch(LINEUP_ENDPOINT_ID, GAME_FEED_URL.format(game_pk=context.game_pk), {}, snapshot_dir,
               f"live-feed-{context.game_pk}", transport, timestamp)
        for context in contexts if context.game_pk is not None
    ]


def _collect_savant(game_date: str, snapshot_dir: Path, transport: HttpTransport, entries: Mapping[str, Any], timestamp: str) -> CollectedResponse:
    _authorized(entries, SAVANT_ENDPOINT_ID)
    season = int(game_date[:4])
    return _fetch(SAVANT_ENDPOINT_ID, expected_stats_source_url(season), {}, snapshot_dir,
                  f"savant-expected-statistics-{season}", transport, timestamp)


def _authorized(entries: Mapping[str, Any], endpoint_id: str):
    entry = entries.get(endpoint_id)
    if entry is None or entry.implementation_status != "AUTOMATED":
        raise SlateCollectionError(f"registry-blocked source: {endpoint_id}")
    return entry


def _fetch(endpoint_id: str, url: str, params: dict[str, str | int], snapshot_dir: Path, stem: str, transport: HttpTransport, timestamp: str) -> CollectedResponse:
    try:
        response = transport.get(url, params=params or None, timeout=_HTTP_TIMEOUT_SECONDS)
    except (OSError, requests.RequestException) as error:
        return CollectedResponse(endpoint_id, url, params, timestamp, "UNVERIFIED", "RETRYABLE", None, None, None, f"transport failed: {error}", "RETRYABLE")
    raw_bytes = getattr(response, "content", None)
    if not isinstance(raw_bytes, bytes):
        return CollectedResponse(endpoint_id, url, params, timestamp, "UNVERIFIED", "TERMINAL", None, None, None, "response has no byte body", "TERMINAL")
    digest = hashlib.sha256(raw_bytes).hexdigest()
    snapshot_path = _write_snapshot(snapshot_dir, stem, raw_bytes, digest)
    status_code = getattr(response, "status_code", None)
    if status_code != 200:
        if status_code == 429:
            failure, reason = "RETRYABLE", "rate_limited_response: source returned HTTP 429"
        else:
            failure = "RETRYABLE" if isinstance(status_code, int) and status_code >= 500 else "TERMINAL"
            reason = f"source returned HTTP {status_code}"
        return CollectedResponse(endpoint_id, url, params, timestamp, "UNVERIFIED", failure, raw_bytes, digest, snapshot_path, reason, failure)
    return CollectedResponse(endpoint_id, url, params, timestamp, "PASS", "PASS", raw_bytes, digest, snapshot_path, None, "PASS")


def _attach_schedule_probables(fixture: dict[str, Any], contexts: Sequence[GameContext]) -> None:
    schedule = fixture["provenance"][SCHEDULE_ENDPOINT_ID]
    evidence = []
    for context in contexts:
        for side in ("away", "home"):
            player_id = getattr(context, f"probable_{side}_pitcher_mlbam_id")
            if player_id is not None:
                evidence.append({
                    "game_pk": context.game_pk, "team_side": side, "kind": "probable", "player_mlbam_id": player_id,
                    "retrieved_at_utc": schedule["retrieved_at_utc"],
                    "source_freshness_status": schedule["source_freshness_status"],
                    "source_failure_status": schedule["source_failure_status"],
                    "endpoint_or_url": schedule["endpoint_or_url"], "raw_response_hash": schedule["raw_response_hash"],
                })
    if not evidence:
        _leave_unverified(fixture, PROBABLE_ENDPOINT_ID, "schedule contains no probable pitcher MLBAM IDs")
        return
    _merge_starter_evidence(fixture, evidence)
    canonical = {"game_pk": sorted({record["game_pk"] for record in evidence}), "opponent_pitcher_mlbam_id": sorted({record["player_mlbam_id"] for record in evidence})}
    provenance = dict(schedule)
    provenance["row_count_when_relevant"] = len(evidence)
    provenance["canonical_keys"] = canonical
    _set_healthy_source(fixture, PROBABLE_ENDPOINT_ID, provenance)


def _attach_live_feed_inputs(fixture: dict[str, Any], fixture_parent: Path, contexts: Sequence[GameContext], responses: Sequence[CollectedResponse]) -> None:
    by_game = {context.game_pk: context for context in contexts}
    successful = [response for response in responses if response.status == "PASS" and response.raw_bytes is not None]
    payloads = fixture.setdefault("lineup_payloads", {})
    if not isinstance(payloads, dict):
        raise SlateCollectionError("fixture lineup_payloads must be an object")
    confirmed_ids: set[int] = set()
    actual_evidence: list[dict[str, Any]] = []
    for response in successful:
        game_pk = _game_pk_from_live_url(response.endpoint_or_url)
        context = by_game.get(game_pk)
        if context is None:
            continue
        try:
            payload = json.loads(response.raw_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        live_status = _live_game_status(payload)
        if live_status is not None:
            fixture.setdefault("lineup_game_statuses", {})[str(game_pk)] = live_status
        lineups = normalize_game_lineups(context, payload, response.retrieved_at_utc)
        if lineups.lineup_fetch_status == "fetched" and response.snapshot_path is not None:
            existing = payloads.get(str(game_pk))
            selected = _side_payload_references(existing)
            relative_snapshot = os.path.relpath(response.snapshot_path, fixture_parent)
            for side, team in (("away", lineups.away_lineup), ("home", lineups.home_lineup)):
                if team.lineup_status == "confirmed":
                    selected[side] = {
                        "snapshot_path": relative_snapshot,
                        "retrieved_at_utc": response.retrieved_at_utc,
                        "raw_response_hash": response.raw_response_hash,
                    }
                    confirmed_ids.update(player.player_mlbam_id for player in team.players if player.player_mlbam_id is not None)
            if selected:
                payloads[str(game_pk)] = selected
        actual_evidence.extend(_actual_starters(context, payload, response))
    if successful:
        hashes = {str(_game_pk_from_live_url(row.endpoint_or_url)): row.raw_response_hash for row in successful}
        _set_healthy_source(fixture, LINEUP_ENDPOINT_ID, _provenance_from_many(
            successful, "MLB Stats API", len(confirmed_ids), {"game_pk": sorted(_game_pk_from_live_url(row.endpoint_or_url) for row in successful if _game_pk_from_live_url(row.endpoint_or_url) is not None), "player_mlbam_id": sorted(confirmed_ids)}, hashes,
        ))
    else:
        reason = next((row.reason for row in responses if row.reason), "no live feed response was collected")
        _leave_unverified(fixture, LINEUP_ENDPOINT_ID, reason)
    if actual_evidence:
        _merge_starter_evidence(fixture, actual_evidence)
    _append_lineup_observations(fixture, responses)


def _validate_live_feed_response(
    contexts: Sequence[GameContext], response: CollectedResponse,
) -> CollectedResponse:
    """Validate feed shape while retaining side-level confirmation independently."""

    if response.status != "PASS" or response.raw_bytes is None:
        return response
    game_pk = _game_pk_from_live_url(response.endpoint_or_url)
    context = next((item for item in contexts if item.game_pk == game_pk), None)
    if context is None:
        return _unverified_live_feed(response, "invalid_response")
    try:
        payload = json.loads(response.raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _unverified_live_feed(response, "invalid_response")
    if not isinstance(payload, Mapping):
        return _unverified_live_feed(response, "invalid_response")
    lineups = normalize_game_lineups(context, payload, response.retrieved_at_utc)
    if lineups.lineup_fetch_status != "fetched":
        return _unverified_live_feed(response, "invalid_response")
    sides = {
        "away": lineups.away_lineup.lineup_status.upper(),
        "home": lineups.home_lineup.lineup_status.upper(),
    }
    confirmed = [side for side, status in sides.items() if status == "CONFIRMED"]
    if len(confirmed) == 2:
        return replace(response, parser_status="PASS", attachment_status="PASS", lineup_side_statuses=sides)
    if confirmed:
        return replace(response, parser_status="PASS", attachment_status="PARTIAL", reason="partial_official_batting_order", lineup_side_statuses=sides)
    return replace(response, parser_status="PASS", attachment_status="UNVERIFIED", reason="no_complete_official_batting_order", lineup_side_statuses=sides)


def _unverified_live_feed(response: CollectedResponse, reason: str) -> CollectedResponse:
    return replace(
        response,
        source_freshness_status="UNVERIFIED",
        source_failure_status="UNVERIFIED",
        reason=reason,
        parser_status="UNVERIFIED",
        attachment_status="UNVERIFIED",
    )


def _attach_savant_input(fixture: dict[str, Any], game_date: str, response: CollectedResponse) -> None:
    if response.status != "PASS" or response.raw_bytes is None:
        if response.parser_status == "UNVERIFIED":
            _mark_unusable_source(fixture, SAVANT_ENDPOINT_ID, response.reason or "Expected Statistics response is malformed")
        else:
            _leave_unverified(fixture, SAVANT_ENDPOINT_ID, response.reason or "Expected Statistics was unavailable")
        return
    season = int(game_date[:4])
    parsed = parse_expected_stats_payload(season, response.raw_bytes, response.endpoint_or_url, response.retrieved_at_utc)
    if parsed.fetch_status != "fetched":
        _leave_unverified(fixture, SAVANT_ENDPOINT_ID, "Expected Statistics response is malformed")
        return
    try:
        payload = response.raw_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        _leave_unverified(fixture, SAVANT_ENDPOINT_ID, "Expected Statistics response is not UTF-8")
        return
    player_ids = sorted({row.player_mlbam_id for row in parsed.rows if row.player_mlbam_id is not None})
    fixture["expected_statistics"] = {"season": season, "source_url": response.endpoint_or_url, "payload": payload}
    _set_healthy_source(fixture, SAVANT_ENDPOINT_ID, _provenance(
        response, "Baseball Savant", len(parsed.rows), {"player_mlbam_id": player_ids, "season": season},
    ))


def _validate_savant_response(game_date: str, response: CollectedResponse) -> CollectedResponse:
    """Separate successful transport from parser-valid, attachable source data."""

    if response.status != "PASS" or response.raw_bytes is None:
        return response
    try:
        parsed = parse_expected_stats_payload(
            int(game_date[:4]), response.raw_bytes, response.endpoint_or_url, response.retrieved_at_utc,
        )
    except UnicodeDecodeError as error:
        return replace(response, source_freshness_status="UNVERIFIED", source_failure_status="UNVERIFIED",
                       reason=f"invalid_response: {error}", parser_status="UNVERIFIED", attachment_status="UNVERIFIED")
    if parsed.fetch_status != "fetched":
        return replace(response, source_freshness_status="UNVERIFIED", source_failure_status="UNVERIFIED",
                       reason=_savant_parser_failure_reason(response.raw_bytes, parsed.error_message),
                       parser_status="UNVERIFIED", attachment_status="UNVERIFIED")
    try:
        response.raw_bytes.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        return replace(response, source_freshness_status="UNVERIFIED", source_failure_status="UNVERIFIED",
                       reason=f"invalid_response: {error}", parser_status="UNVERIFIED", attachment_status="UNVERIFIED")
    return replace(response, parser_status="PASS", attachment_status="PASS")


def _savant_parser_failure_reason(raw_bytes: bytes, parser_error: str | None) -> str:
    """Classify unusable bytes without weakening the Expected Statistics parser."""

    content = raw_bytes.lstrip()
    if not content:
        return "empty_response"
    lowered = content[:512].lower()
    if lowered.startswith(b"<!doctype html") or lowered.startswith(b"<html"):
        return "html_response"
    if parser_error == "payload contains no rows":
        return "empty_response"
    return f"parser_schema_mismatch: {parser_error or 'response is malformed'}"


def _actual_starters(context: GameContext, payload: Mapping[str, Any], response: CollectedResponse) -> list[dict[str, Any]]:
    teams = _mapping(_mapping(_mapping(payload.get("liveData")).get("boxscore")).get("teams"))
    result: list[dict[str, Any]] = []
    for side in ("away", "home"):
        team = _mapping(teams.get(side))
        players = _mapping(team.get("players"))
        starters = []
        for player in players.values():
            record = _mapping(player)
            pitching = _mapping(_mapping(record.get("stats")).get("pitching"))
            if _integer(pitching.get("gamesStarted")) == 1:
                player_id = _positive_integer(_mapping(record.get("person")).get("id"))
                if player_id is not None:
                    starters.append(player_id)
        if len(starters) == 1:
            result.append({
                "game_pk": context.game_pk, "team_side": side, "kind": "actual", "player_mlbam_id": starters[0],
                "retrieved_at_utc": response.retrieved_at_utc, "source_freshness_status": response.source_freshness_status,
                "source_failure_status": response.source_failure_status, "endpoint_or_url": response.endpoint_or_url,
                "raw_response_hash": response.raw_response_hash,
            })
    return result


def _merge_starter_evidence(fixture: dict[str, Any], additions: Sequence[Mapping[str, Any]]) -> None:
    existing = fixture.get("starter_evidence", [])
    if not isinstance(existing, list):
        raise SlateCollectionError("fixture starter_evidence must be an array")
    merged = [dict(record) for record in existing if isinstance(record, Mapping)]
    for candidate in additions:
        key = tuple(candidate.get(field) for field in ("game_pk", "team_side", "kind"))
        matches = [index for index, record in enumerate(merged) if tuple(record.get(field) for field in ("game_pk", "team_side", "kind")) == key]
        if matches:
            current = merged[matches[0]]
            if _record_health(current) == "PASS" and _record_health(candidate) != "PASS":
                continue
            merged[matches[0]] = dict(candidate)
        else:
            merged.append(dict(candidate))
    fixture["starter_evidence"] = merged


def _set_healthy_source(fixture: dict[str, Any], endpoint_id: str, provenance: Mapping[str, Any]) -> None:
    current = _mapping(_mapping(fixture.get("provenance")).get(endpoint_id))
    if current and _provenance_health(current) == "PASS" and _provenance_health(provenance) != "PASS":
        return
    fixture.setdefault("provenance", {})[endpoint_id] = dict(provenance)
    fixture.setdefault("unavailable_source_inputs", {}).pop(endpoint_id, None)


def _leave_unverified(fixture: dict[str, Any], endpoint_id: str, reason: str) -> None:
    current = _mapping(_mapping(fixture.get("provenance")).get(endpoint_id))
    if current and _provenance_health(current) == "PASS":
        return
    if current:
        return
    fixture.setdefault("unavailable_source_inputs", {})[endpoint_id] = {"status": "UNVERIFIED", "reason": reason}


def _mark_unusable_source(fixture: dict[str, Any], endpoint_id: str, reason: str) -> None:
    """Remove a previously misclassified attachment without deleting its raw snapshot."""

    fixture.setdefault("provenance", {}).pop(endpoint_id, None)
    fixture.pop("expected_statistics", None)
    fixture.setdefault("unavailable_source_inputs", {})[endpoint_id] = {"status": "UNVERIFIED", "reason": reason}


def _finalize_unavailable_inputs(fixture: dict[str, Any]) -> None:
    provenance = _mapping(fixture.get("provenance"))
    unavailable = fixture.setdefault("unavailable_source_inputs", {})
    if not isinstance(unavailable, dict):
        raise SlateCollectionError("fixture unavailable_source_inputs must be an object")
    for endpoint_id in CONTRIBUTING_ENDPOINT_IDS:
        if endpoint_id in provenance:
            unavailable.pop(endpoint_id, None)
        else:
            unavailable.setdefault(endpoint_id, {"status": "UNVERIFIED", "reason": "source was not collected"})


def _provenance(response: CollectedResponse, source: str, row_count: int, canonical_keys: Mapping[str, Any]) -> dict[str, Any]:
    if response.raw_response_hash is None:
        raise SlateCollectionError("cannot create provenance without raw response bytes")
    return {
        "source": source, "endpoint_or_url": response.endpoint_or_url,
        "submitted_parameters": response.submitted_parameters, "retrieved_at_utc": response.retrieved_at_utc,
        "raw_response_hash": response.raw_response_hash, "row_count_when_relevant": row_count,
        "canonical_keys": dict(canonical_keys), "source_freshness_status": response.source_freshness_status,
        "source_failure_status": response.source_failure_status,
    }


def _provenance_from_many(responses: Sequence[CollectedResponse], source: str, row_count: int, canonical_keys: Mapping[str, Any], hashes: Mapping[str, str | None]) -> dict[str, Any]:
    first = responses[0]
    provenance = _provenance(first, source, row_count, canonical_keys)
    # The required field remains one actual raw SHA-256.  The explicit mapping
    # preserves every other independent feed without inventing an aggregate.
    provenance["raw_response_hash_by_game_pk"] = {key: value for key, value in sorted(hashes.items()) if value is not None}
    provenance["raw_snapshot_by_game_pk"] = {
        str(_game_pk_from_live_url(response.endpoint_or_url)): {
            "endpoint_or_url": response.endpoint_or_url,
            "retrieved_at_utc": response.retrieved_at_utc,
            "snapshot_path": str(response.snapshot_path),
            "raw_response_hash": response.raw_response_hash,
        }
        for response in responses if response.snapshot_path is not None and response.raw_response_hash is not None
    }
    provenance["aggregation_manifest_reference"] = str(first.snapshot_path.parent / "collection_manifest.json") if first.snapshot_path else "collection_manifest.json"
    provenance["submitted_parameters"] = {"per_game": {key: {} for key in sorted(hashes)}}
    return provenance


def _terminal(response: CollectedResponse, reason: str) -> CollectedResponse:
    return CollectedResponse(response.endpoint_id, response.endpoint_or_url, response.submitted_parameters, response.retrieved_at_utc, "UNVERIFIED", "TERMINAL", response.raw_bytes, response.raw_response_hash, response.snapshot_path, reason)


def _write_snapshot(directory: Path, stem: str, raw_bytes: bytes, digest: str) -> Path:
    path = directory / f"{stem}-{digest}.raw"
    if path.exists():
        if path.read_bytes() != raw_bytes:
            raise SlateCollectionError(f"immutable snapshot collision: {path}")
        return path
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=directory)
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        temporary.write_bytes(raw_bytes)
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return path


def _atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        temporary.write_text(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _report_row(response: CollectedResponse) -> dict[str, Any]:
    return {
        "endpoint_id": response.endpoint_id, "status": response.status, "reason": response.reason,
        "endpoint_or_url": response.endpoint_or_url, "submitted_parameters": response.submitted_parameters,
        "retrieved_at_utc": response.retrieved_at_utc, "raw_response_hash": response.raw_response_hash,
        "snapshot": str(response.snapshot_path) if response.snapshot_path else None,
        "fetch_status": response.fetch_status, "parser_status": response.parser_status,
        "attachment_status": response.attachment_status,
        "lineup_side_statuses": dict(response.lineup_side_statuses or {}),
    }


def _side_payload_references(existing: Any) -> dict[str, Any]:
    """Convert legacy whole-game paths to side references without losing evidence."""

    if isinstance(existing, str) and existing:
        return {"away": existing, "home": existing}
    if isinstance(existing, Mapping):
        return {
            side: existing[side]
            for side in ("away", "home")
            if existing.get(side) is not None
        }
    return {}


def _append_lineup_observations(
    fixture: dict[str, Any], responses: Sequence[CollectedResponse],
) -> None:
    """Retain every raw feed attempt; selected side evidence is never overwritten by it."""

    observations = fixture.setdefault("lineup_observations", [])
    if not isinstance(observations, list):
        raise SlateCollectionError("fixture lineup_observations must be an array")
    known = {
        (record.get("game_pk"), record.get("raw_response_hash"))
        for record in observations if isinstance(record, Mapping)
    }
    for response in responses:
        game_pk = _game_pk_from_live_url(response.endpoint_or_url)
        key = (game_pk, response.raw_response_hash)
        if game_pk is None or response.raw_response_hash is None or key in known:
            continue
        observations.append({
            "game_pk": game_pk,
            "endpoint_or_url": response.endpoint_or_url,
            "retrieved_at_utc": response.retrieved_at_utc,
            "snapshot_path": str(response.snapshot_path) if response.snapshot_path else None,
            "raw_response_hash": response.raw_response_hash,
            "source_freshness_status": response.source_freshness_status,
            "source_failure_status": response.source_failure_status,
            "parser_status": response.parser_status,
            "attachment_status": response.attachment_status,
            "lineup_side_statuses": dict(response.lineup_side_statuses or {}),
            "reason": response.reason,
        })
        known.add(key)


def _record_health(record: Mapping[str, Any]) -> str:
    return _computed_status(str(record.get("source_freshness_status")), str(record.get("source_failure_status")))


def _game_pk_from_live_url(url: str) -> int | None:
    parts = url.rstrip("/").split("/")
    try:
        return int(parts[-3]) if parts[-2:] == ["feed", "live"] else None
    except ValueError:
        return None


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _live_game_status(payload: Mapping[str, Any]) -> str | None:
    status = _mapping(_mapping(payload.get("gameData")).get("status"))
    value = status.get("detailedState") or status.get("abstractGameState")
    return value if isinstance(value, str) and value else None


def _positive_integer(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _integer(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return int(value) if isinstance(value, str) and value.isdigit() else None


def _iso_date(value: str) -> str:
    try:
        return calendar_date.fromisoformat(value).isoformat()
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be YYYY-MM-DD") from error


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    raise SystemExit(main())
