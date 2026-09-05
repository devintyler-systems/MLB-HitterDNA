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
    serialize_filter_table,
)
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
from hitterdna.statsapi import GameContext, normalize_schedule_contexts


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
    provenance = _validate_provenance(fixture.get("provenance"), entries)
    schedule_health = _provenance_health(provenance["mlb_statsapi_schedule_by_date"])
    run_source_status = _computed_status(
        *(_provenance_health(provenance[endpoint_id]) for endpoint_id in CONTRIBUTING_ENDPOINT_IDS)
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
            context, lineup_payloads, provenance, timestamp, fixture_path,
        )
        for context in contexts
    }
    starters = _starter_context(contexts, fixture.get("starter_evidence", []), provenance)
    expected_rows, observations = _expected_observations(
        fixture, provenance, game_date, fixture_path,
    )
    tables, exclusions, queue_records = _filter_and_queue(
        contexts, lineups_by_game, observations, fixture, game_date, provenance
    )
    unavailable = [
        {"endpoint_id": entry.endpoint_id, "implementation_status": entry.implementation_status,
         "retrieval_status": "UNVERIFIED", "reason": "not retrieved by fixture-backed Slate Run v0"}
        for entry in entries.values() if entry.implementation_status != "AUTOMATED"
    ]
    source_records = [
        {"endpoint_id": endpoint_id, "provenance": provenance[endpoint_id],
         "retrieval_status": _provenance_health(provenance[endpoint_id])}
        for endpoint_id in sorted(provenance)
    ] + unavailable
    artifact_statuses = _artifact_statuses(provenance, exclusions)
    base = {"schema_version": "slate-run-v0", "game_date": game_date, "generated_at_utc": timestamp,
            "provenance": provenance}
    artifacts = {
        "run_manifest": {**base, "artifact_type": "run_manifest", "status": artifact_statuses["run_manifest"], "data": {"fixture_path": str(fixture_path), "network_mode": "disabled", "artifact_types": list(ARTIFACT_TYPES), "unavailable_sources": unavailable}},
        "slate_context": {**base, "artifact_type": "slate_context", "status": artifact_statuses["slate_context"], "data": {"games": [_context_record(c) for c in contexts]}},
        "confirmed_lineups": {**base, "artifact_type": "confirmed_lineups", "status": artifact_statuses["confirmed_lineups"], "data": {"games": [_lineup_record(lineups_by_game[c.game_pk]) for c in contexts]}},
        "starter_context": {**base, "artifact_type": "starter_context", "status": artifact_statuses["starter_context"], "data": {"games": starters}},
        "expected_statistics": {**base, "artifact_type": "expected_statistics", "status": artifact_statuses["expected_statistics"], "data": {"rows": expected_rows}},
        "filter_table": {**base, "artifact_type": "filter_table", "status": artifact_statuses["filter_table"], "data": {"candidates": tables}},
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


def _validate_provenance(raw: Any, entries: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    if not isinstance(raw, Mapping):
        raise SlateRunValidationError("fixture provenance is missing")
    required_ids = {"mlb_statsapi_schedule_by_date", "mlb_statsapi_lineup_confirmation", "mlb_statsapi_probable_pitchers", "savant_expected_statistics"}
    if not required_ids.issubset(raw):
        raise SlateRunValidationError("fixture provenance misses required source evidence")
    result: dict[str, dict[str, Any]] = {}
    for endpoint_id, item in raw.items():
        if endpoint_id not in entries or not isinstance(item, Mapping) or set(item) != REQUIRED_PROVENANCE:
            raise SlateRunValidationError(f"malformed provenance for {endpoint_id}")
        if entries[endpoint_id].implementation_status != "AUTOMATED":
            raise SlateRunValidationError(f"non-automated contract cannot be input: {endpoint_id}")
        try:
            validate_source_health_statuses(str(item["source_freshness_status"]), str(item["source_failure_status"]))
        except RegistryValidationError as error:
            raise SlateRunValidationError(f"malformed provenance for {endpoint_id}: {error}") from error
        if not isinstance(item["canonical_keys"], Mapping) or not item["endpoint_or_url"] or not item["raw_response_hash"]:
            raise SlateRunValidationError(f"malformed provenance for {endpoint_id}")
        result[str(endpoint_id)] = dict(item)
    return result


def _resolve_fixture_embedded_path(fixture_path: Path, configured_path: str | Path) -> Path:
    """Resolve a fixture reference relative to its containing fixture file."""

    path = Path(configured_path)
    return path if path.is_absolute() else fixture_path.parent / path


def _lineups_for_context(context: GameContext, payloads: Mapping[str, Any], provenance: Mapping[str, Mapping[str, Any]], timestamp: str, fixture_path: Path) -> GameLineups:
    payload = payloads.get(str(context.game_pk))
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
    evidence = provenance["mlb_statsapi_lineup_confirmation"]
    if evidence["source_freshness_status"] != "PASS" or evidence["source_failure_status"] != "PASS":
        return _empty_lineups(context, timestamp)
    return lineups


def _empty_lineups(context: GameContext, timestamp: str) -> GameLineups:
    return GameLineups(context.game_pk, _team_lineup(context, "away"), _team_lineup(context, "home"), "", timestamp, context.pregame_eligibility, "unavailable")


def _team_lineup(context: GameContext, side: str):
    from hitterdna.lineups import TeamLineup
    return TeamLineup(getattr(context, f"{side}_team_id"), getattr(context, f"{side}_team") or "", "unconfirmed", ())


def _starter_context(contexts: Sequence[GameContext], evidence: Any, provenance: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(evidence, list): raise SlateRunValidationError("starter_evidence must be an array")
    result = []
    for context in contexts:
        records = [x for x in evidence if isinstance(x, Mapping) and x.get("game_pk") == context.game_pk]
        sides = []
        for side in ("away", "home"):
            applicable = [dict(x) for x in records if x.get("team_side") == side and x.get("kind") in {"probable", "actual"}]
            for record in applicable:
                record["source_health_status"] = _starter_health(record, provenance)
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


def _expected_observations(fixture: Mapping[str, Any], provenance: Mapping[str, Mapping[str, Any]], game_date: str, fixture_path: Path):
    raw = fixture.get("expected_statistics")
    if not isinstance(raw, Mapping): raise SlateRunValidationError("expected_statistics fixture is missing")
    if _provenance_health(provenance["savant_expected_statistics"]) != "PASS":
        return [], {}
    season = int(raw.get("season", game_date[:4]))
    parsed = parse_expected_stats_payload(season, raw.get("payload", ""), str(raw.get("source_url", "")), provenance["savant_expected_statistics"]["retrieved_at_utc"])
    if parsed.fetch_status != "fetched": raise SlateRunValidationError("expected_statistics fixture is malformed")
    policy_path = raw.get("stabilization_policy_path")
    loaded = load_stabilization_policy_file(
        _resolve_fixture_embedded_path(fixture_path, policy_path)
    ) if policy_path else None
    if loaded is None or loaded.load_status != "loaded": raise SlateRunValidationError("stabilization policy is missing or malformed")
    registry = StabilizationRegistry(loaded.policies)
    observations = {row.player_mlbam_id: {key: apply_stabilization_to_observation(value, registry, season) for key, value in expected_stats_to_observations(row).items()} for row in parsed.rows if row.player_mlbam_id is not None}
    return [asdict(row) for row in parsed.rows], observations


def _filter_and_queue(contexts, lineups_by_game, observations, fixture, game_date, provenance):
    raw = fixture.get("filter")
    if not isinstance(raw, Mapping): raise SlateRunValidationError("filter fixture is missing")
    definitions = tuple(FilterDefinition(**item) for item in raw.get("definitions", []))
    thresholds = ThresholdRegistry(raw.get("thresholds", {}), str(raw.get("threshold_source_url", "")), str(raw.get("threshold_retrieved_at_utc", "")), str(raw.get("threshold_version", "")))
    tables: list[dict[str, Any]] = []; exclusions: list[dict[str, Any]] = []; queue: list[dict[str, Any]] = []
    for context in contexts:
        lineups = lineups_by_game[context.game_pk]
        for side, opponent in (("away", context.home_team or ""), ("home", context.away_team or "")):
            team = getattr(lineups, f"{side}_lineup")
            if not team.players:
                lineup_health = _provenance_health(provenance["mlb_statsapi_lineup_confirmation"])
                exclusions.append({"game_pk": context.game_pk, "team_side": side, "status": lineup_health if lineup_health != "PASS" else "UNVERIFIED", "reason": "lineup evidence is not confirmed"})
                continue
            for player in team.players:
                table = build_candidate_filter_table(game_date, context, player, opponent, definitions, observations.get(player.player_mlbam_id, {}), thresholds)
                serialized = serialize_filter_table(table); tables.append(serialized)
                if table.candidate_disposition != "advance":
                    exclusions.append({"game_pk": context.game_pk, "player_mlbam_id": player.player_mlbam_id, "status": "UNVERIFIED" if any(r.status == "UNVERIFIED" for r in table.results) else "FAIL", "reason": "; ".join(r.reason for r in table.results if r.status != "PASS")})
                    continue
                record = {"queue_id": _queue_id(game_date, context.game_pk, player.player_mlbam_id), "analysis_date": game_date, "game_pk": context.game_pk, "player_mlbam_id": player.player_mlbam_id, "candidate_name": player.player_name, "team": player.team_abbreviation, "opponent": opponent, "prop_family": "hit", "source_type": "savant", "trigger_type": "other", "raw_claim": "stabilized expected-statistics observation passed declared filter", "source_url": provenance["savant_expected_statistics"]["endpoint_or_url"], "retrieved_at_utc": provenance["savant_expected_statistics"]["retrieved_at_utc"], "sample_type": "pa", "sample_n": observations[player.player_mlbam_id][definitions[0].metric_key].sample_n, "stabilization_status": "pass", "feature_mapping": ["hitter_baseline"], "expected_direction": "positive", "duplicate_risk": "low", "evidence_status": "verified", "lineup_status": "confirmed", "market_status": "unpriced", "disposition": "advance"}
                decision = validate_discovery_record(record)
                if decision.valid: queue.append(record)
                else: exclusions.append({"game_pk": context.game_pk, "player_mlbam_id": player.player_mlbam_id, "status": "FAIL", "reason": "; ".join(decision.reasons)})
    return tables, exclusions, queue


def _context_record(context: GameContext) -> dict[str, Any]:
    return {"game_pk": context.game_pk, "venue_mlbam_id": context.venue_id, "game_status": context.game_status, "pregame_eligibility": context.pregame_eligibility, "away_team": context.away_team, "home_team": context.home_team}

def _lineup_record(lineups: GameLineups) -> dict[str, Any]:
    return {"game_pk": lineups.game_pk, "fetch_status": lineups.lineup_fetch_status, "away": [asdict(x) for x in lineups.away_lineup.players], "home": [asdict(x) for x in lineups.home_lineup.players]}

def _starter_health(record: Mapping[str, Any], provenance: Mapping[str, Mapping[str, Any]]) -> str:
    """Return health attached to the retained evidence, with fixture provenance fallback."""

    freshness = record.get("source_freshness_status")
    failure = record.get("source_failure_status")
    if freshness is None or failure is None:
        source = provenance["mlb_statsapi_probable_pitchers"]
        freshness = source["source_freshness_status"]
        failure = source["source_failure_status"]
    if freshness != "PASS":
        return str(freshness) if freshness in HEALTH else "UNVERIFIED"
    return str(failure) if failure in HEALTH else "UNVERIFIED"


def _starter_unavailable_status(records: Sequence[Mapping[str, Any]]) -> str:
    statuses = {str(record.get("source_health_status")) for record in records}
    for status in ("STALE", "RETRYABLE", "TERMINAL"):
        if status in statuses:
            return status
    return "UNVERIFIED"


def _contributing_source_observations(provenance: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Serialize only fixture evidence consumed by this vertical slice."""

    return [
        {
            "endpoint_id": endpoint_id,
            "provenance": provenance[endpoint_id],
            "retrieval_status": _provenance_health(provenance[endpoint_id]),
        }
        for endpoint_id in CONTRIBUTING_ENDPOINT_IDS
    ]


def _provenance_health(provenance: Mapping[str, Any]) -> str:
    return _computed_status(
        str(provenance["source_freshness_status"]),
        str(provenance["source_failure_status"]),
    )


def _computed_status(*statuses: str) -> str:
    """Return the documented deterministic status precedence for known inputs."""

    values = {status if status in HEALTH else "UNVERIFIED" for status in statuses}
    return next(status for status in STATUS_PRECEDENCE if status in values) if values else "PASS"


def _artifact_statuses(
    provenance: Mapping[str, Mapping[str, Any]], exclusions: Sequence[Mapping[str, Any]],
) -> dict[str, str]:
    """Compute statuses from source health and source-evidence exclusions only."""

    source = {endpoint_id: _provenance_health(provenance[endpoint_id]) for endpoint_id in CONTRIBUTING_ENDPOINT_IDS}
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
