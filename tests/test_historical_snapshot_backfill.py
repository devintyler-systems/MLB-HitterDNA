import hashlib
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from hitterdna.historical_snapshot_backfill import (
    build_historical_snapshot_backfill_manifest,
    validate_historical_snapshot_backfill_manifest,
)
from hitterdna.build_historical_snapshot_backfill import main as build_cli
from hitterdna.statsapi import normalize_schedule_contexts
from hitterdna.lineups import normalize_game_lineups
from hitterdna.historical_pregame_training_ledger import build_historical_pregame_training_ledger


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "historical_snapshot_backfill"
SCHEMA_PATH = Path(__file__).parents[1] / "schemas" / "historical_snapshot_backfill_manifest.schema.json"


def _index() -> dict:
    return json.loads((FIXTURE_ROOT / "valid_index.json").read_text(encoding="utf-8"))


def _write_index(tmp_path: Path, value: dict, *, root: Path = FIXTURE_ROOT) -> tuple[Path, Path]:
    target_root = tmp_path / "snapshots"
    target_root.mkdir(parents=True)
    for name in ("schedule.json", "pregame.json", "final.json"):
        (target_root / name).write_bytes((root / name).read_bytes())
    path = tmp_path / "index.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    return path, target_root


def _build(tmp_path: Path, mutate=None):
    value = _index()
    if mutate:
        mutate(value)
    index_path, root = _write_index(tmp_path, value)
    return build_historical_snapshot_backfill_manifest(index_path, root)


def _build_variant(tmp_path: Path, pregame_transform=None, final_transform=None):
    value = _index()
    root = tmp_path / "snapshots"
    root.mkdir(parents=True)
    for name in ("schedule.json", "pregame.json", "final.json"):
        (root / name).write_bytes((FIXTURE_ROOT / name).read_bytes())
    if pregame_transform:
        payload = json.loads((root / "pregame.json").read_text(encoding="utf-8"))
        pregame_transform(payload)
        raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        (root / "pregame.json").write_bytes(raw)
        value["games"][0]["snapshots"][1]["raw_response_hash"] = hashlib.sha256(raw).hexdigest()
    if final_transform:
        payload = json.loads((root / "final.json").read_text(encoding="utf-8"))
        final_transform(payload)
        raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        (root / "final.json").write_bytes(raw)
        value["games"][0]["snapshots"][2]["raw_response_hash"] = hashlib.sha256(raw).hexdigest()
    index_path = tmp_path / "index.json"
    index_path.write_text(json.dumps(value), encoding="utf-8")
    return build_historical_snapshot_backfill_manifest(index_path, root)


def _both_sides(payload, action):
    teams = payload["liveData"]["boxscore"]["teams"]
    action(teams["away"])
    action(teams["home"])


def test_valid_triplet_recomputes_hashes_is_strict_schema_and_deterministic(tmp_path):
    schedule_payload = json.loads((FIXTURE_ROOT / "schedule.json").read_text(encoding="utf-8"))
    context = normalize_schedule_contexts(schedule_payload)[0]
    assert context.game_pk == 900001 and context.analysis_date == "2025-06-01" and context.scheduled_start_utc == "2025-06-01T23:00:00Z"
    pregame_payload = json.loads((FIXTURE_ROOT / "pregame.json").read_text(encoding="utf-8"))
    lineups = normalize_game_lineups(context, pregame_payload, "2025-06-01T21:00:00Z")
    assert lineups.away_lineup.lineup_status == "confirmed" and lineups.home_lineup.lineup_status == "confirmed"
    first = _build(tmp_path / "one")
    second = _build(tmp_path / "one-repeat")
    validate_historical_snapshot_backfill_manifest(first)
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(first)
    assert {key: value for key, value in first.items() if key != "snapshot_root_reference"} == {key: value for key, value in second.items() if key != "snapshot_root_reference"}
    assert first["coverage_summary"] == {
        "games_offered": 1,
        "complete_triplets": 1,
        "valid_pregame_snapshots": 1,
        "valid_ledger_eligible_games": 1,
        "excluded_games_by_reason": {},
        "duplicate_snapshot_conflicts": 0,
        "source_health_distribution": {"PASS/PASS": 3},
    }
    assert [item["role"] for item in first["games"][0]["snapshots"]] == ["schedule", "pregame_live_feed", "final_box_score"]
    for item in first["provenance_references"]:
        raw = (FIXTURE_ROOT / item["snapshot_path"]).read_bytes()
        assert item["raw_response_hash"] == hashlib.sha256(raw).hexdigest()
    invalid = dict(first)
    invalid["unexpected"] = True
    with pytest.raises(Exception):
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(invalid)


@pytest.mark.parametrize(
    ("name", "mutate", "reason"),
    [
        ("missing_role", lambda value: value["games"][0]["snapshots"].pop(), "MISSING_SNAPSHOT_ROLE"),
        ("post_cutoff", lambda value: value["games"][0]["snapshots"][1].update(retrieved_at_utc="2025-06-01T22:01:00Z"), "PREGAME_SNAPSHOT_RETRIEVED_AFTER_AS_OF_UTC"),
        ("hash_mismatch", lambda value: value["games"][0]["snapshots"][0].update(raw_response_hash="0" * 64), "RAW_HASH_MISMATCH"),
        ("duplicate_role", lambda value: value["games"][0]["snapshots"].append(dict(value["games"][0]["snapshots"][1])), "DUPLICATE_CONFLICTING_ROLE_SNAPSHOT"),
        ("traversal", lambda value: value["games"][0]["snapshots"][0].update(snapshot_path="../schedule.json"), "PATH_TRAVERSAL_OR_MISSING_SNAPSHOT"),
        ("stale", lambda value: value["games"][0]["snapshots"][0].update(source_freshness_status="STALE"), "STALE_SOURCE_EVIDENCE"),
        ("retryable", lambda value: value["games"][0]["snapshots"][0].update(source_failure_status="RETRYABLE"), "RETRYABLE_SOURCE_EVIDENCE"),
        ("terminal", lambda value: value["games"][0]["snapshots"][0].update(source_failure_status="TERMINAL"), "TERMINAL_SOURCE_EVIDENCE"),
        ("unverified", lambda value: value["games"][0]["snapshots"][0].update(source_freshness_status="UNVERIFIED"), "UNVERIFIED_SOURCE_EVIDENCE"),
        ("not_outcome_only", lambda value: value["games"][0]["snapshots"][2].update(outcome_only=False), "FINAL_BOX_NOT_OUTCOME_ONLY"),
        ("cutoff", lambda value: value["games"][0].update(as_of_utc="2025-06-01T23:00:00Z"), "PREDICTION_CUTOFF_NOT_STRICTLY_PREGAME"),
    ],
)
def test_invalid_triplets_fail_closed(tmp_path, name, mutate, reason):
    manifest = _build(tmp_path / name, mutate)
    assert manifest["games"] == []
    assert any(item["reason"] == reason for item in manifest["exclusions"])


def test_malformed_payload_and_cross_game_identity_are_exclusions(tmp_path):
    value = _index()
    value["games"][0]["snapshots"][2]["raw_response_hash"] = hashlib.sha256(b'{"gamePk":999999,"teams":{}}').hexdigest()
    root = tmp_path / "snapshots"
    root.mkdir(parents=True)
    for name in ("schedule.json", "pregame.json"):
        (root / name).write_bytes((FIXTURE_ROOT / name).read_bytes())
    (root / "final.json").write_bytes(b'{"gamePk":999999,"teams":{}}')
    path = tmp_path / "index.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    result = build_historical_snapshot_backfill_manifest(path, root)
    assert any(item["reason"] == "MISMATCHED_GAME_PK" for item in result["exclusions"])


def test_malformed_json_is_retained_as_source_exclusion(tmp_path):
    value = _index()
    malformed = b"not-json"
    root = tmp_path / "snapshots"
    root.mkdir(parents=True)
    (root / "schedule.json").write_bytes(malformed)
    for name in ("pregame.json", "final.json"):
        (root / name).write_bytes((FIXTURE_ROOT / name).read_bytes())
    value["games"][0]["snapshots"][0]["raw_response_hash"] = hashlib.sha256(malformed).hexdigest()
    path = tmp_path / "index.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    result = build_historical_snapshot_backfill_manifest(path, root)
    assert any(item["reason"] == "MALFORMED_SOURCE_EVIDENCE" for item in result["exclusions"])


def test_manifest_paths_are_explicit_and_no_network_surface_exists(tmp_path):
    result = _build(tmp_path)
    assert all(not Path(item["snapshot_path"]).is_absolute() for item in result["provenance_references"])
    source = (Path(__file__).parents[1] / "src" / "hitterdna" / "historical_snapshot_backfill.py").read_text(encoding="utf-8")
    assert "requests" not in source and "httpx" not in source and "urllib" not in source and "socket" not in source
    assert "display_name" not in source and "player_name" not in source


def test_explicit_local_cli_writes_only_requested_output(tmp_path):
    output = tmp_path / "manifest.json"
    assert build_cli(["--index", str(FIXTURE_ROOT / "valid_index.json"), "--snapshot-root", str(FIXTURE_ROOT), "--output", str(output)]) == 0
    result = json.loads(output.read_text(encoding="utf-8"))
    validate_historical_snapshot_backfill_manifest(result)
    assert result["coverage_summary"]["complete_triplets"] == 1


@pytest.mark.parametrize("variant", [
    lambda payload: payload["liveData"]["boxscore"]["teams"].pop("home"),
    lambda payload: payload["liveData"]["boxscore"]["teams"]["home"].update(battingOrder=[]),
    lambda payload: payload["liveData"]["boxscore"]["teams"]["home"].update(battingOrder=[201,202,203,204,205,206,207,208]),
])
def test_complete_away_with_missing_empty_or_partial_home_retains_away_only(tmp_path, variant):
    result = _build_variant(tmp_path, pregame_transform=variant)
    pregame = result["games"][0]["snapshots"][1]
    assert result["coverage_summary"]["valid_pregame_snapshots"] == 1
    assert pregame["away_lineup_status"] == "confirmed" and pregame["away_confirmed_hitter_count"] == 9
    assert pregame["home_lineup_status"] == "unconfirmed" and pregame["home_confirmed_hitter_count"] == 0


@pytest.mark.parametrize("variant", [
    lambda payload: _both_sides(payload, lambda team: team.update(battingOrder=[])),
    lambda payload: _both_sides(payload, lambda team: team.update(battingOrder=[101,102,103,104,105,106,107,108])),
    lambda payload: _both_sides(payload, lambda team: team.update(battingOrder=[101,102,103,104,105,106,107,108,109,101])),
    lambda payload: _both_sides(payload, lambda team: team.update(battingOrder=["101",102,103,104,105,106,107,108,109])),
    lambda payload: _both_sides(payload, lambda team: team.update(battingOrder=[{},102,103,104,105,106,107,108,109])),
    lambda payload: _both_sides(payload, lambda team: team.update(battingOrder=[0,102,103,104,105,106,107,108,109])),
    lambda payload: _both_sides(payload, lambda team: team["players"][next(iter(team["players"]))]["person"].update(id=999)),
])
def test_noncanonical_or_unconfirmed_orders_are_not_retained(tmp_path, variant):
    result = _build_variant(tmp_path, pregame_transform=variant)
    assert result["games"] == []
    assert any(item["reason"] == "PARTIAL_OR_MISSING_OFFICIAL_LINEUP" for item in result["exclusions"])


def test_unordered_player_mapping_does_not_override_canonical_batting_order(tmp_path):
    def reverse_players(payload):
        teams = payload["liveData"]["boxscore"]["teams"]
        teams["away"]["players"] = dict(reversed(list(teams["away"]["players"].items())))
    result = _build_variant(tmp_path, pregame_transform=reverse_players)
    pregame = result["games"][0]["snapshots"][1]
    assert pregame["away_lineup_status"] == "confirmed" and pregame["home_lineup_status"] == "confirmed"


def _ledger_input_from_manifest(manifest):
    game = manifest["games"][0]
    records = {item["role"]: item for item in game["snapshots"]}
    root = Path(manifest["snapshot_root_reference"])
    def payload(role):
        return json.loads((root / records[role]["snapshot_path"]).read_text(encoding="utf-8"))
    return {
        "generated_at_utc": manifest["generated_at_utc"],
        "schedule": payload("schedule"),
        "schedule_provenance": records["schedule"],
        "pregame_snapshots": [{"game_pk": game["game_pk"], "as_of_utc": game["as_of_utc"], "available_at_utc": records["pregame_live_feed"]["available_at_utc"], "provenance": records["pregame_live_feed"], "payload": payload("pregame_live_feed")}],
        "final_box_scores": [{"game_pk": game["game_pk"], "provenance": records["final_box_score"], "payload": payload("final_box_score")}],
    }


def test_manifest_to_ledger_handoff_keeps_only_confirmed_away_and_final_is_outcome_only(tmp_path):
    def partial_home(payload):
        payload["liveData"]["boxscore"]["teams"]["home"]["battingOrder"] = [201,202,203,204,205,206,207,208]
    manifest = _build_variant(tmp_path, pregame_transform=partial_home, final_transform=lambda payload: payload["teams"]["away"]["batters"].append(999) or payload["teams"]["away"]["players"].update({"ID999": {"person": {"id": 999}, "stats": {"batting": {"hits": 1}}}}))
    assert manifest["games"][0]["snapshots"][1]["away_confirmed_hitter_count"] == 9
    result = build_historical_pregame_training_ledger(_ledger_input_from_manifest(manifest))
    assert result["row_count"] == 9
    assert {row["player_mlbam_id"] for row in result["rows"]} == set(range(101, 110))
    assert not ({201,202,203,204,205,206,207,208,209} & {row["player_mlbam_id"] for row in result["rows"]})
    assert any(item["reason"] == "FINAL_HITTER_NOT_IN_PREGAME_CONFIRMED_LINEUP" and item["player_mlbam_id"] == 999 for item in result["exclusions"])
    assert result["rows"][0]["final_label_provenance"]["outcome_only"] is True
    assert all(row["opponent_starter_mlbam_id"] is None for row in result["rows"])
    assert all("player_name" not in row for row in result["rows"])


@pytest.mark.parametrize("field", ["away_lineup_status", "home_lineup_status", "away_confirmed_hitter_count", "home_confirmed_hitter_count"])
def test_schema_requires_each_side_level_pregame_field(tmp_path, field):
    result = _build(tmp_path)
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    invalid = json.loads(json.dumps(result))
    invalid["games"][0]["snapshots"][1].pop(field)
    with pytest.raises(Exception):
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(invalid)


@pytest.mark.parametrize("field,value", [("away_lineup_status", "bogus"), ("home_lineup_status", "bogus"), ("away_confirmed_hitter_count", 10), ("home_confirmed_hitter_count", -1)])
def test_schema_rejects_invalid_side_level_status_or_count(tmp_path, field, value):
    result = _build(tmp_path)
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    invalid = json.loads(json.dumps(result))
    invalid["games"][0]["snapshots"][1][field] = value
    with pytest.raises(Exception):
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(invalid)
