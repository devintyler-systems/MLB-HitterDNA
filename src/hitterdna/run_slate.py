"""Deterministic, fixture-backed Slate Run v0 orchestration.

This module intentionally has no HTTP dependency.  Its only intake is a local
JSON fixture, so it is suitable for repeatable local verification and cannot
accidentally promote an unregistered retrieval contract.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import date as calendar_date
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any, Mapping, Sequence

from jsonschema import Draft202012Validator, FormatChecker, ValidationError

from hitterdna.discovery_queue import validate_discovery_record
from hitterdna.filter_table import (
    FilterDefinition, ThresholdRegistry, build_candidate_filter_table,
    build_policy_unverified_filter_table,
    serialize_filter_table,
)
from hitterdna.filter_policy import load_filter_policy_contract
from hitterdna.lineups import GameLineups, LineupPlayer, normalize_game_lineups
from hitterdna.savant_expected_stats import (
    expected_stats_to_observations, parse_expected_stats_payload,
)
from hitterdna.source_endpoint_registry import (
    RegistryValidationError, load_source_endpoint_registry, validate_source_health_statuses,
)
from hitterdna.stabilization import (
    StabilizationRegistry, apply_stabilization_to_observation, load_stabilization_policy_file,
)
from hitterdna.statsapi import GameContext, classify_pregame_eligibility, normalize_schedule_contexts


ARTIFACT_TYPES = (
    "run_manifest", "slate_context", "confirmed_lineups", "starter_context",
    "expected_statistics", "filter_table", "discovery_queue", "exclusions",
    "validation_report",
)
REQUIRED_PROVENANCE = frozenset({
    "source", "endpoint_or_url", "submitted_parameters", "retrieved_at_utc",
    "raw_response_hash", "row_count_when_relevant", "canonical_keys",
    "source_freshness_status", "source_failure_status",
})
LINEUP_AGGREGATION_FIELDS = frozenset({
    "raw_response_hash_by_game_pk", "raw_snapshot_by_game_pk", "aggregation_manifest_reference",
})
HEALTH = {"PASS", "FAIL", "UNVERIFIED", "STALE", "RETRYABLE", "TERMINAL"}
STATUS_PRECEDENCE = ("TERMINAL", "RETRYABLE", "STALE", "UNVERIFIED", "FAIL", "PASS")
CONTRIBUTING_ENDPOINT_IDS = (
    "mlb_statsapi_schedule_by_date",
    "mlb_statsapi_lineup_confirmation",
    "mlb_statsapi_probable_pitchers",
    "savant_expected_statistics",
)


class SlateRunValidationError(ValueError):
    """A fixture cannot be safely promoted into a slate package."""


def build_slate_run(game_date: str, fixture_path: Path) -> dict[str, Any]:
    """Build every artifact in memory, failing before any output is written."""

    fixture_path = fixture_path.resolve()
    fixture = _load_fixture(fixture_path)
    timestamp = _required_text(fixture, "retrieved_at_utc")
    if fixture.get("game_date") != game_date:
        raise SlateRunValidationError("fixture game_date does not match --date")
    entries = {entry.endpoint_id: entry for entry in load_source_endpoint_registry()}
    provenance, unavailable_inputs = _validate_provenance(
        fixture.get("provenance"), fixture.get("unavailable_source_inputs"), entries,
    )
    schedule_health = _provenance_health(provenance["mlb_statsapi_schedule_by_date"])
    run_source_status = _computed_status(
        *(_source_health(provenance, unavailable_inputs, endpoint_id) for endpoint_id in CONTRIBUTING_ENDPOINT_IDS)
    )
    contexts: tuple[GameContext, ...] = ()
    if schedule_health == "PASS" and run_source_status != "TERMINAL":
        schedule = fixture.get("schedule")
        if not isinstance(schedule, Mapping):
            raise SlateRunValidationError("fixture schedule is missing")
        contexts = tuple(sorted(normalize_schedule_contexts(dict(schedule)), key=lambda item: item.game_pk or 0))
        if not contexts or any(context.game_pk is None for context in contexts):
            raise SlateRunValidationError("fixture schedule has no canonical game_pk")

    lineup_payloads = fixture.get("lineup_payloads", {})
    if not isinstance(lineup_payloads, Mapping):
        raise SlateRunValidationError("lineup_payloads must be an object")
    lineups_by_game = {
        context.game_pk: _lineups_for_context(
            context, lineup_payloads, provenance, unavailable_inputs, timestamp, fixture_path,
        )
        for context in contexts
    }
    starters = _starter_context(contexts, fixture.get("starter_evidence", []), provenance, unavailable_inputs)
    expected_rows, observations = _expected_observations(
        fixture, provenance, unavailable_inputs, game_date, fixture_path,
    )
    policy = load_filter_policy_contract(fixture.get("filter"), loaded_at_utc=timestamp)
    tables, exclusions, queue_records = _filter_and_queue(
        contexts, lineups_by_game, observations, policy, fixture, game_date, provenance, unavailable_inputs
    )
    exclusions.extend(_unavailable_input_exclusions(unavailable_inputs))
    registry_unavailable = [
        {"endpoint_id": entry.endpoint_id, "implementation_status": entry.implementation_status,
         "retrieval_status": "UNVERIFIED", "reason": "not retrieved by fixture-backed Slate Run v0"}
        for entry in entries.values() if entry.implementation_status != "AUTOMATED"
    ]
    unavailable = [
        {"endpoint_id": endpoint_id, "implementation_status": entries[endpoint_id].implementation_status,
         "retrieval_status": value["status"], "reason": value["reason"]}
        for endpoint_id, value in sorted(unavailable_inputs.items())
    ] + registry_unavailable
    source_records = [
        {"endpoint_id": endpoint_id, "provenance": provenance[endpoint_id],
         "retrieval_status": _provenance_health(provenance[endpoint_id])}
        for endpoint_id in sorted(provenance)
    ] + unavailable
    artifact_statuses = _artifact_statuses(provenance, unavailable_inputs, exclusions)
    base = {"schema_version": "slate-run-v0", "game_date": game_date, "generated_at_utc": timestamp,
            "provenance": provenance}
    artifacts = {
        "run_manifest": {**base, "artifact_type": "run_manifest", "status": artifact_statuses["run_manifest"], "data": {"fixture_path": str(fixture_path), "network_mode": "disabled", "artifact_types": list(ARTIFACT_TYPES), "unavailable_sources": unavailable}},
        "slate_context": {**base, "artifact_type": "slate_context", "status": artifact_statuses["slate_context"], "data": {"games": [_context_record(c) for c in contexts]}},
        "confirmed_lineups": {**base, "artifact_type": "confirmed_lineups", "status": artifact_statuses["confirmed_lineups"], "data": {"games": [_lineup_record(lineups_by_game[c.game_pk]) for c in contexts]}},
        "starter_context": {**base, "artifact_type": "starter_context", "status": artifact_statuses["starter_context"], "data": {"games": starters}},
        "expected_statistics": {**base, "artifact_type": "expected_statistics", "status": artifact_statuses["expected_statistics"], "data": {"rows": expected_rows}},
        "filter_table": {**base, "artifact_type": "filter_table", "status": artifact_statuses["filter_table"], "data": {"policy_provenance": _policy_provenance(policy), "candidates": tables}},
        "discovery_queue": {**base, "artifact_type": "discovery_queue", "status": artifact_statuses["discovery_queue"], "data": {"records": queue_records}},
        "exclusions": {**base, "artifact_type": "exclusions", "status": artifact_statuses["exclusions"], "data": {"records": exclusions}},
        "validation_report": {**base, "artifact_type": "validation_report", "status": artifact_statuses["validation_report"], "data": {"status": artifact_statuses["validation_report"], "registry_status": "PASS", "source_contracts": source_records}},
    }
    return artifacts


def write_slate_run(artifacts: Mapping[str, Any], output: Path) -> None:
    """Validate all bytes first, then atomically replace the output directory."""

    source_observations = _contributing_source_observations(artifacts["run_manifest"]["provenance"])
    _validate_artifacts(artifacts, source_observations)
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=output.parent))
    try:
        for name, artifact in artifacts.items():
            (temporary / f"{name}.json").write_text(_stable_json(artifact), encoding="utf-8")
        source_lines = "".join(_stable_json(row) for row in source_observations)
        (temporary / "source_observations.jsonl").write_text(source_lines, encoding="utf-8")
        _replace_directory(temporary, output)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a fixture-backed HitterDNA Slate Run v0 package")
    parser.add_argument("--date", required=True, type=_iso_date)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--fixtures", required=True, type=Path,
        help="Local JSON fixture; networking is never enabled",
    )
    args = parser.parse_args(argv)
    try:
        write_slate_run(build_slate_run(args.date, args.fixtures), args.output)
    except (OSError, ValueError, RegistryValidationError) as error:
        parser.error(str(error))
    return 0


def _load_fixture(path: Path) -> Mapping[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SlateRunValidationError(f"fixture is unreadable: {error}") from error
    if not isinstance(payload, Mapping):
        raise SlateRunValidationError("fixture must be an object")
    return payload


def _validate_provenance(
    raw: Any, unavailable_raw: Any, entries: Mapping[str, Any],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, str]]]:
    if not isinstance(raw, Mapping):
        raise SlateRunValidationError("fixture provenance is missing")
    required_schedule = "mlb_statsapi_schedule_by_date"
    allowed_ids = set(CONTRIBUTING_ENDPOINT_IDS)
    if required_schedule not in raw:
        raise SlateRunValidationError("fixture provenance misses schedule source evidence")
    if set(raw) - allowed_ids:
        raise SlateRunValidationError("fixture provenance contains an unsupported source input")
    result: dict[str, dict[str, Any]] = {}
    for endpoint_id, item in raw.items():
        if endpoint_id not in entries or not isinstance(item, Mapping):
            raise SlateRunValidationError(f"malformed provenance for {endpoint_id}")
        allowed_fields = REQUIRED_PROVENANCE | (LINEUP_AGGREGATION_FIELDS if endpoint_id == "mlb_statsapi_lineup_confirmation" else frozenset())
        if set(item) not in {REQUIRED_PROVENANCE, allowed_fields}:
            raise SlateRunValidationError(f"malformed provenance for {endpoint_id}")
        if entries[endpoint_id].implementation_status != "AUTOMATED":
            raise SlateRunValidationError(f"non-automated contract cannot be input: {endpoint_id}")
        try:
            validate_source_health_statuses(str(item["source_freshness_status"]), str(item["source_failure_status"]))
        except RegistryValidationError as error:
            raise SlateRunValidationError(f"malformed provenance for {endpoint_id}: {error}") from error
        if not isinstance(item["canonical_keys"], Mapping) or not item["endpoint_or_url"] or not _is_sha256(item["raw_response_hash"]):
            raise SlateRunValidationError(f"malformed provenance for {endpoint_id}")
        if endpoint_id == "mlb_statsapi_lineup_confirmation" and set(item) == allowed_fields:
            _validate_lineup_aggregation(item)
        result[str(endpoint_id)] = dict(item)
    unavailable = _validate_unavailable_source_inputs(unavailable_raw, result)
    return result, unavailable


def _validate_unavailable_source_inputs(
    raw: Any, provenance: Mapping[str, Mapping[str, Any]],
) -> dict[str, dict[str, str]]:
    """Accept explicit capability gaps without inventing a source observation."""

    missing = set(CONTRIBUTING_ENDPOINT_IDS) - set(provenance)
    if not missing:
        if raw is None:
            return {}
        if not isinstance(raw, Mapping) or raw:
            raise SlateRunValidationError("unavailable_source_inputs must list only missing source inputs")
        return {}
    if not isinstance(raw, Mapping) or set(raw) != missing:
        raise SlateRunValidationError("fixture must explicitly mark every missing source input UNVERIFIED")
    result: dict[str, dict[str, str]] = {}
    for endpoint_id, value in raw.items():
        if not isinstance(value, Mapping) or set(value) != {"status", "reason"}:
            raise SlateRunValidationError(f"malformed unavailable source input for {endpoint_id}")
        if value.get("status") != "UNVERIFIED" or not isinstance(value.get("reason"), str) or not value["reason"]:
            raise SlateRunValidationError(f"malformed unavailable source input for {endpoint_id}")
        result[str(endpoint_id)] = {"status": "UNVERIFIED", "reason": str(value["reason"])}
    return result


def _resolve_fixture_embedded_path(fixture_path: Path, configured_path: str | Path) -> Path:
    """Resolve a fixture reference relative to its containing fixture file."""

    path = Path(configured_path)
    return path if path.is_absolute() else fixture_path.parent / path


def _lineups_for_context(context: GameContext, payloads: Mapping[str, Any], provenance: Mapping[str, Mapping[str, Any]], unavailable_inputs: Mapping[str, Mapping[str, str]], timestamp: str, fixture_path: Path) -> GameLineups:
    payload = payloads.get(str(context.game_pk))
    if isinstance(payload, Mapping) and set(payload).issubset({"away", "home"}):
        return _lineups_from_side_payloads(
            context, payload, provenance, unavailable_inputs, timestamp, fixture_path,
        )
    if isinstance(payload, str):
        try:
            payload = json.loads(
                _resolve_fixture_embedded_path(fixture_path, payload).read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError):
            payload = None
    if not isinstance(payload, Mapping):
        return _empty_lineups(context, timestamp)
    lineups = normalize_game_lineups(context, dict(payload), timestamp)
    if _source_health(provenance, unavailable_inputs, "mlb_statsapi_lineup_confirmation") != "PASS":
        return _empty_lineups(context, timestamp)
    return lineups


def _lineups_from_side_payloads(
    context: GameContext,
    payloads: Mapping[str, Any],
    provenance: Mapping[str, Mapping[str, Any]],
    unavailable_inputs: Mapping[str, Mapping[str, str]],
    timestamp: str,
    fixture_path: Path,
) -> GameLineups:
    """Load each selected side from its own complete raw-feed observation."""

    if _source_health(provenance, unavailable_inputs, "mlb_statsapi_lineup_confirmation") != "PASS":
        return _empty_lineups(context, timestamp)
    selected: dict[str, Any] = {}
    source_url = ""
    retrieved_at = timestamp
    for side in ("away", "home"):
        reference = payloads.get(side)
        path, side_timestamp = _lineup_payload_reference(reference, timestamp)
        if path is None:
            continue
        try:
            raw = json.loads(_resolve_fixture_embedded_path(fixture_path, path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(raw, Mapping):
            continue
        lineups = normalize_game_lineups(context, dict(raw), side_timestamp)
        team = getattr(lineups, f"{side}_lineup")
        if team.lineup_status == "confirmed":
            selected[side] = team
            source_url = lineups.source_url
            retrieved_at = side_timestamp
    result = _empty_lineups(context, retrieved_at)
    return GameLineups(
        context.game_pk,
        selected.get("away", result.away_lineup),
        selected.get("home", result.home_lineup),
        source_url,
        retrieved_at,
        context.pregame_eligibility,
        "fetched" if selected else "unavailable",
    )


def _lineup_payload_reference(value: Any, fallback_timestamp: str) -> tuple[str | None, str]:
    if isinstance(value, str) and value:
        return value, fallback_timestamp
    if not isinstance(value, Mapping):
        return None, fallback_timestamp
    path = value.get("snapshot_path")
    timestamp = value.get("retrieved_at_utc")
    return (
        str(path) if isinstance(path, str) and path else None,
        str(timestamp) if isinstance(timestamp, str) and timestamp else fallback_timestamp,
    )


def _empty_lineups(context: GameContext, timestamp: str) -> GameLineups:
    return GameLineups(context.game_pk, _team_lineup(context, "away"), _team_lineup(context, "home"), "", timestamp, context.pregame_eligibility, "unavailable")


def _team_lineup(context: GameContext, side: str):
    from hitterdna.lineups import TeamLineup
    return TeamLineup(getattr(context, f"{side}_team_id"), getattr(context, f"{side}_team") or "", "unconfirmed", ())


def _starter_context(contexts: Sequence[GameContext], evidence: Any, provenance: Mapping[str, Mapping[str, Any]], unavailable_inputs: Mapping[str, Mapping[str, str]]) -> list[dict[str, Any]]:
    if not isinstance(evidence, list): raise SlateRunValidationError("starter_evidence must be an array")
    result = []
    for context in contexts:
        records = [x for x in evidence if isinstance(x, Mapping) and x.get("game_pk") == context.game_pk]
        sides = []
        for side in ("away", "home"):
            applicable = [dict(x) for x in records if x.get("team_side") == side and x.get("kind") in {"probable", "actual"}]
            for record in applicable:
                record["source_health_status"] = _starter_health(record, provenance, unavailable_inputs)
            valid = [record for record in applicable if record["source_health_status"] == "PASS"]
            valid.sort(key=lambda x: (
                x.get("kind") != "actual", str(x.get("retrieved_at_utc", "")),
                x.get("player_mlbam_id") or 0,
            ))
            selected = valid[0] if valid else None
            sides.append({"team_side": side, "selected": selected, "evidence": applicable,
                          "status": "PASS" if selected else _starter_unavailable_status(applicable)})
        result.append({"game_pk": context.game_pk, "starters": sides})
    return result


def _expected_observations(fixture: Mapping[str, Any], provenance: Mapping[str, Mapping[str, Any]], unavailable_inputs: Mapping[str, Mapping[str, str]], game_date: str, fixture_path: Path):
    raw = fixture.get("expected_statistics")
    if _source_health(provenance, unavailable_inputs, "savant_expected_statistics") != "PASS":
        return [], {}
    if not isinstance(raw, Mapping): raise SlateRunValidationError("expected_statistics fixture is missing")
    season = int(raw.get("season", game_date[:4]))
    parsed = parse_expected_stats_payload(season, raw.get("payload", ""), str(raw.get("source_url", "")), provenance["savant_expected_statistics"]["retrieved_at_utc"])
    if parsed.fetch_status != "fetched": raise SlateRunValidationError("expected_statistics fixture is malformed")
    policy_path = raw.get("stabilization_policy_path")
    loaded = load_stabilization_policy_file(
        _resolve_fixture_embedded_path(fixture_path, policy_path)
    ) if policy_path else None
    raw_observations = {
        row.player_mlbam_id: expected_stats_to_observations(row)
        for row in parsed.rows if row.player_mlbam_id is not None
    }
    # An availability-only policy gate may inspect the raw, source-backed
    # observation. Numeric and stabilization-dependent filters still fail
    # closed because those observations retain stabilization_status=unverified.
    if loaded is None or loaded.load_status != "loaded":
        return [asdict(row) for row in parsed.rows], raw_observations
    registry = StabilizationRegistry(loaded.policies)
    observations = {row.player_mlbam_id: {key: apply_stabilization_to_observation(value, registry, season) for key, value in expected_stats_to_observations(row).items()} for row in parsed.rows if row.player_mlbam_id is not None}
    return [asdict(row) for row in parsed.rows], observations


def _filter_and_queue(contexts, lineups_by_game, observations, policy, fixture, game_date, provenance, unavailable_inputs):
    raw = policy.contract or {}
    definitions = tuple(FilterDefinition(**item) for item in raw.get("definitions", []))
    thresholds = ThresholdRegistry(
        raw.get("thresholds", {}), str(raw.get("policy_source_reference", "")),
        str(raw.get("policy_loaded_at_utc", "")), str(raw.get("policy_version", "")),
        str(raw.get("policy_content_sha256", "")),
    )
    live_statuses = fixture.get("lineup_game_statuses", {})
    if not isinstance(live_statuses, Mapping):
        raise SlateRunValidationError("lineup_game_statuses must be an object")
    tables: list[dict[str, Any]] = []; exclusions: list[dict[str, Any]] = []; queue: list[dict[str, Any]] = []
    for context in contexts:
        lineups = lineups_by_game[context.game_pk]
        live_status = live_statuses.get(str(context.game_pk))
        effective_status = live_status if isinstance(live_status, str) and live_status else context.game_status
        effective_eligibility = classify_pregame_eligibility(effective_status)
        for side, opponent in (("away", context.home_team or ""), ("home", context.away_team or "")):
            team = getattr(lineups, f"{side}_lineup")
            if effective_eligibility not in {"eligible_refresh", "urgent_refresh"}:
                exclusions.append({
                    "game_pk": context.game_pk,
                    "team_side": side,
                    "status": "FAIL",
                    "reason": _pregame_ineligibility_reason(effective_status),
                })
                continue
            if not team.players:
                lineup_health = _source_health(provenance, unavailable_inputs, "mlb_statsapi_lineup_confirmation")
                exclusions.append({"game_pk": context.game_pk, "team_side": side, "status": lineup_health if lineup_health != "PASS" else "UNVERIFIED", "reason": "lineup evidence is not confirmed"})
                continue
            for player in team.players:
                if policy.load_status != "loaded":
                    table = build_policy_unverified_filter_table(
                        game_date, context, player, opponent, policy.reason or "FILTER_POLICY_UNVERIFIED"
                    )
                else:
                    table = build_candidate_filter_table(game_date, context, player, opponent, definitions, observations.get(player.player_mlbam_id, {}), thresholds)
                serialized = serialize_filter_table(table); tables.append(serialized)
                if table.candidate_disposition != "advance":
                    exclusions.append({"game_pk": context.game_pk, "player_mlbam_id": player.player_mlbam_id, "status": "UNVERIFIED" if any(r.status == "UNVERIFIED" for r in table.results) else "FAIL", "reason": "; ".join(r.reason for r in table.results if r.status != "PASS")})
                    continue
                required_are_availability_only = all(
                    definition.operator == "present" for definition in definitions if definition.required
                )
                record = {"queue_id": _queue_id(game_date, context.game_pk, player.player_mlbam_id), "analysis_date": game_date, "game_pk": context.game_pk, "player_mlbam_id": player.player_mlbam_id, "candidate_name": player.player_name, "team": player.team_abbreviation, "opponent": opponent, "prop_family": "hit", "source_type": "savant", "trigger_type": "other", "raw_claim": "source-backed expected-statistics observations passed declared filter policy", "source_url": provenance["savant_expected_statistics"]["endpoint_or_url"], "retrieved_at_utc": provenance["savant_expected_statistics"]["retrieved_at_utc"], "sample_type": "pa", "sample_n": observations[player.player_mlbam_id][definitions[0].metric_key].sample_n, "stabilization_status": "not_applicable" if required_are_availability_only else observations[player.player_mlbam_id][definitions[0].metric_key].stabilization_status, "feature_mapping": ["hitter_baseline"], "expected_direction": "positive", "duplicate_risk": "low", "evidence_status": "verified", "lineup_status": "confirmed", "market_status": "unpriced", "disposition": "advance"}
                decision = validate_discovery_record(record)
                if decision.valid: queue.append(record)
                else: exclusions.append({"game_pk": context.game_pk, "player_mlbam_id": player.player_mlbam_id, "status": "FAIL", "reason": "; ".join(decision.reasons)})
    return tables, exclusions, queue


def _policy_provenance(policy) -> dict[str, Any]:
    if policy.contract is not None:
        return {
            "load_status": "loaded",
            "source_reference": policy.contract["policy_source_reference"],
            "content_sha256": policy.contract["policy_content_sha256"],
            "loaded_at_utc": policy.contract["policy_loaded_at_utc"],
            "policy_version": policy.contract["policy_version"],
        }
    return {"load_status": "unverified", "reason": policy.reason or "FILTER_POLICY_UNVERIFIED"}


def _pregame_ineligibility_reason(game_status: str | None) -> str:
    if game_status == "In Progress":
        return "GAME_STARTED: official lineup evidence is retained but pregame discovery is ineligible"
    if game_status == "Final":
        return "FINAL: official lineup evidence is retained but pregame discovery is ineligible"
    return "PREGAME_INELIGIBLE: official lineup evidence is retained but pregame discovery is ineligible"


def _context_record(context: GameContext) -> dict[str, Any]:
    return {"game_pk": context.game_pk, "venue_mlbam_id": context.venue_id, "game_status": context.game_status, "pregame_eligibility": context.pregame_eligibility, "away_team": context.away_team, "home_team": context.home_team}

def _lineup_record(lineups: GameLineups) -> dict[str, Any]:
    # This artifact is a confirmation surface: partial feed order entries are
    # retained only in the raw snapshot, never presented as confirmed hitters.
    away = lineups.away_lineup.players if lineups.away_lineup.lineup_status == "confirmed" else ()
    home = lineups.home_lineup.players if lineups.home_lineup.lineup_status == "confirmed" else ()
    return {"game_pk": lineups.game_pk, "fetch_status": lineups.lineup_fetch_status, "away": [asdict(x) for x in away], "home": [asdict(x) for x in home]}

def _starter_health(record: Mapping[str, Any], provenance: Mapping[str, Mapping[str, Any]], unavailable_inputs: Mapping[str, Mapping[str, str]]) -> str:
    """Return health attached to the retained evidence, with fixture provenance fallback."""

    freshness = record.get("source_freshness_status")
    failure = record.get("source_failure_status")
    if freshness is None or failure is None:
        return _source_health(provenance, unavailable_inputs, "mlb_statsapi_probable_pitchers")
    if freshness != "PASS":
        return str(freshness) if freshness in HEALTH else "UNVERIFIED"
    return str(failure) if failure in HEALTH else "UNVERIFIED"


def _starter_unavailable_status(records: Sequence[Mapping[str, Any]]) -> str:
    statuses = {str(record.get("source_health_status")) for record in records}
    for status in ("STALE", "RETRYABLE", "TERMINAL"):
        if status in statuses:
            return status
    return "UNVERIFIED"


def _unavailable_input_exclusions(
    unavailable_inputs: Mapping[str, Mapping[str, str]],
) -> list[dict[str, str]]:
    """Expose capability gaps without pretending they are player evidence."""

    return [
        {"source_input": endpoint_id, "status": value["status"], "reason": value["reason"]}
        for endpoint_id, value in sorted(unavailable_inputs.items())
    ]


def _contributing_source_observations(provenance: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Serialize only fixture evidence consumed by this vertical slice."""

    rows: list[dict[str, Any]] = []
    for endpoint_id in CONTRIBUTING_ENDPOINT_IDS:
        if endpoint_id not in provenance:
            continue
        source = provenance[endpoint_id]
        hashes = source.get("raw_response_hash_by_game_pk") if endpoint_id == "mlb_statsapi_lineup_confirmation" else None
        snapshots = source.get("raw_snapshot_by_game_pk") if isinstance(hashes, Mapping) else None
        if isinstance(hashes, Mapping) and isinstance(snapshots, Mapping):
            for game_pk, raw_hash in sorted(hashes.items(), key=lambda item: int(item[0])):
                snapshot = snapshots[game_pk]
                item = {key: value for key, value in source.items() if key not in LINEUP_AGGREGATION_FIELDS}
                item.update({
                    "endpoint_or_url": snapshot["endpoint_or_url"],
                    "retrieved_at_utc": snapshot["retrieved_at_utc"],
                    "raw_response_hash": raw_hash,
                    "canonical_keys": {"game_pk": int(game_pk)},
                })
                rows.append({"endpoint_id": endpoint_id, "provenance": item, "retrieval_status": _provenance_health(item)})
        else:
            rows.append({"endpoint_id": endpoint_id, "provenance": source, "retrieval_status": _provenance_health(source)})
    return rows


def _validate_lineup_aggregation(item: Mapping[str, Any]) -> None:
    hashes = item.get("raw_response_hash_by_game_pk")
    snapshots = item.get("raw_snapshot_by_game_pk")
    if not isinstance(hashes, Mapping) or not isinstance(snapshots, Mapping) or set(hashes) != set(snapshots):
        raise SlateRunValidationError("malformed lineup per-game provenance")
    if not isinstance(item.get("aggregation_manifest_reference"), str) or not item["aggregation_manifest_reference"]:
        raise SlateRunValidationError("malformed lineup aggregation manifest reference")
    for game_pk, raw_hash in hashes.items():
        if not isinstance(game_pk, str) or not game_pk.isdigit() or int(game_pk) < 1 or not _is_sha256(raw_hash):
            raise SlateRunValidationError("malformed lineup per-game provenance")
        snapshot = snapshots[game_pk]
        if not isinstance(snapshot, Mapping) or set(snapshot) != {"endpoint_or_url", "retrieved_at_utc", "snapshot_path", "raw_response_hash"}:
            raise SlateRunValidationError("malformed lineup per-game provenance")
        if not all(isinstance(snapshot.get(field), str) and snapshot[field] for field in ("endpoint_or_url", "retrieved_at_utc", "snapshot_path")) or snapshot.get("raw_response_hash") != raw_hash:
            raise SlateRunValidationError("malformed lineup per-game provenance")


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _provenance_health(provenance: Mapping[str, Any]) -> str:
    return _computed_status(
        str(provenance["source_freshness_status"]),
        str(provenance["source_failure_status"]),
    )


def _source_health(
    provenance: Mapping[str, Mapping[str, Any]],
    unavailable_inputs: Mapping[str, Mapping[str, str]],
    endpoint_id: str,
) -> str:
    if endpoint_id in provenance:
        return _provenance_health(provenance[endpoint_id])
    if endpoint_id in unavailable_inputs:
        return "UNVERIFIED"
    raise SlateRunValidationError(f"source input state is missing for {endpoint_id}")


def _computed_status(*statuses: str) -> str:
    """Return the documented deterministic status precedence for known inputs."""

    values = {status if status in HEALTH else "UNVERIFIED" for status in statuses}
    return next(status for status in STATUS_PRECEDENCE if status in values) if values else "PASS"


def _artifact_statuses(
    provenance: Mapping[str, Mapping[str, Any]], unavailable_inputs: Mapping[str, Mapping[str, str]], exclusions: Sequence[Mapping[str, Any]],
) -> dict[str, str]:
    """Compute statuses from source health and source-evidence exclusions only."""

    source = {
        endpoint_id: _source_health(provenance, unavailable_inputs, endpoint_id)
        for endpoint_id in CONTRIBUTING_ENDPOINT_IDS
    }
    terminal_gate = "TERMINAL" if "TERMINAL" in source.values() else "PASS"
    def status(*inputs: str) -> str:
        return _computed_status(terminal_gate, *inputs)
    source_exclusions = [
        str(record.get("status", "UNVERIFIED")) for record in exclusions
        if record.get("reason") == "lineup evidence is not confirmed"
    ]
    all_sources = tuple(source.values())
    return {
        "run_manifest": status(*all_sources),
        "slate_context": status(source["mlb_statsapi_schedule_by_date"]),
        "confirmed_lineups": status(source["mlb_statsapi_schedule_by_date"], source["mlb_statsapi_lineup_confirmation"]),
        "starter_context": status(source["mlb_statsapi_schedule_by_date"], source["mlb_statsapi_probable_pitchers"]),
        "expected_statistics": status(source["savant_expected_statistics"]),
        "filter_table": status(source["mlb_statsapi_schedule_by_date"], source["mlb_statsapi_lineup_confirmation"], source["savant_expected_statistics"], *source_exclusions),
        "discovery_queue": status(source["mlb_statsapi_schedule_by_date"], source["mlb_statsapi_lineup_confirmation"], source["savant_expected_statistics"], *source_exclusions),
        "exclusions": status(source["mlb_statsapi_schedule_by_date"], source["mlb_statsapi_lineup_confirmation"], source["savant_expected_statistics"], *source_exclusions),
        "validation_report": status(*all_sources, *source_exclusions),
    }


def _validate_artifacts(
    artifacts: Mapping[str, Any], source_observations: Sequence[Mapping[str, Any]],
) -> None:
    if set(artifacts) != set(ARTIFACT_TYPES): raise SlateRunValidationError("artifact package is incomplete")
    for name, artifact in artifacts.items():
        if artifact.get("artifact_type") != name or artifact.get("status") not in HEALTH or not isinstance(artifact.get("provenance"), Mapping): raise SlateRunValidationError(f"artifact {name} is invalid")
        _validate_schema("slate_run_artifact.schema.json", artifact, f"artifact {name}")
    for row in source_observations:
        _validate_schema("source_observation.schema.json", row, "source observation")


def _validate_schema(schema_name: str, value: Mapping[str, Any], label: str) -> None:
    schema_path = Path(__file__).resolve().parents[2] / "schemas" / schema_name
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except OSError as error:
        raise SlateRunValidationError(
            f"{label} schema validation failed for {schema_name} at {schema_path.resolve()}: "
            "Slate Run v0 requires a source checkout containing the schemas directory"
        ) from error
    try:
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(value)
    except (json.JSONDecodeError, ValidationError) as error:
        raise SlateRunValidationError(f"{label} schema validation failed: {error.message if isinstance(error, ValidationError) else error}") from error

def _replace_directory(temporary: Path, output: Path) -> None:
    backup = output.with_name(f".{output.name}.previous")
    if backup.exists(): shutil.rmtree(backup)
    if output.exists(): os.replace(output, backup)
    try: os.replace(temporary, output)
    except Exception:
        if backup.exists(): os.replace(backup, output)
        raise
    if backup.exists(): shutil.rmtree(backup)

def _stable_json(value: Any) -> str: return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"
def _queue_id(day: str, game_pk: int | None, player_id: int | None) -> str: return hashlib.sha256(f"{day}:{game_pk}:{player_id}".encode()).hexdigest()[:32]
def _required_text(raw: Mapping[str, Any], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value: raise SlateRunValidationError(f"fixture {key} is missing")
    return value
def _iso_date(value: str) -> str:
    try: return calendar_date.fromisoformat(value).isoformat()
    except ValueError as error: raise argparse.ArgumentTypeError("must be YYYY-MM-DD") from error


if __name__ == "__main__":
    raise SystemExit(main())
