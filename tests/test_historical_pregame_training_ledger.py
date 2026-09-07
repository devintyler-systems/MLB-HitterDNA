import ast
import copy
import json
from pathlib import Path
import socket

import jsonschema

from hitterdna.historical_pregame_training_ledger import build_historical_pregame_training_ledger


ROOT = Path(__file__).parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "historical_pregame_training_ledger" / "cases.json"
HASH = "b" * 64


def provenance(endpoint_id: str, url: str, *, retrieved: str, available: str | None = None, freshness="PASS", failure="PASS"):
    result = {"endpoint_id": endpoint_id, "source": "Synthetic MLB Stats API", "endpoint_or_url": url, "submitted_parameters": {}, "retrieved_at_utc": retrieved, "raw_response_hash": HASH, "canonical_keys": {}, "source_freshness_status": freshness, "source_failure_status": failure}
    if available: result["available_at_utc"] = available
    return result


def team(ids, team_id, abbreviation, *, partial=False, mismatch=False):
    order = ids[:-1] if partial else ids
    players = {f"ID{player_id}": {"person": {"id": (999 if mismatch and player_id == ids[0] else player_id)}, "stats": {"batting": {"hits": player_id % 2}}} for player_id in ids}
    return {"team": {"id": team_id, "abbreviation": abbreviation}, "players": players, "battingOrder": order, "batters": ids}


def ledger_input():
    case = json.loads(FIXTURE.read_text())
    away, home = case["away_ids"], case["home_ids"]
    pregame = {"gameData": {"game": {"pk": case["game_pk"]}}, "liveData": {"boxscore": {"teams": {"away": team(away, 11, "AWY"), "home": team(home, 22, "HME")}}}}
    final = {"gamePk": case["game_pk"], "teams": {"away": team(away, 11, "AWY"), "home": team(home, 22, "HME")}}
    return {"generated_at_utc": "2025-06-02T12:00:00Z", "schedule": {"dates": [{"date": "2025-06-01", "games": [case["schedule"]]}]}, "schedule_provenance": provenance("mlb_statsapi_schedule_by_date", "https://example.invalid/schedule", retrieved="2025-06-01T20:00:00Z"), "pregame_snapshots": [{"game_pk": case["game_pk"], "as_of_utc": "2025-06-01T22:00:00Z", "available_at_utc": "2025-06-01T21:00:00Z", "provenance": provenance("mlb_statsapi_live_game_feed", "https://example.invalid/live", retrieved="2025-06-01T21:00:00Z", available="2025-06-01T21:00:00Z"), "payload": pregame, "starter_evidence": {"away": [{"kind": "probable", "player_mlbam_id": 801, "available_at_utc": "2025-06-01T21:00:00Z"}], "home": [{"kind": "probable", "player_mlbam_id": 802, "available_at_utc": "2025-06-01T21:00:00Z"}]} }], "final_box_scores": [{"game_pk": case["game_pk"], "provenance": provenance("mlb_statsapi_box_scores", "https://example.invalid/box", retrieved="2025-06-02T01:00:00Z"), "payload": final}]}


def reasons(result): return {item["reason"] for item in result["exclusions"]}


def test_complete_pregame_lineups_create_one_row_per_confirmed_player_and_preserve_provenance():
    result = build_historical_pregame_training_ledger(ledger_input())
    assert result["row_count"] == 18
    first = result["rows"][0]
    assert first["final_label_provenance"]["raw_response_hash"] == HASH
    assert first["paired_pregame_snapshot_provenance"]["endpoint_id"] == "mlb_statsapi_live_game_feed"
    assert first["paired_pregame_snapshot_provenance"]["endpoint_or_url"] == "https://example.invalid/live"
    assert first["final_label_provenance"]["retrieved_at_utc"] == "2025-06-02T01:00:00Z"
    assert first["official_hit_ge_1"] is True
    assert first["opponent_starter_mlbam_id"] == 802
    assert first["no_probability_status"] == "NO_PROBABILITY_CALCULATED"


def test_partial_side_mismatch_timing_and_missing_label_fail_closed():
    data = ledger_input(); data["pregame_snapshots"][0]["payload"]["liveData"]["boxscore"]["teams"]["home"] = team(list(range(201, 210)), 22, "HME", partial=True)
    result = build_historical_pregame_training_ledger(data)
    assert result["row_count"] == 9 and "PARTIAL_OR_MISSING_OFFICIAL_LINEUP" in reasons(result)
    data = ledger_input(); data["pregame_snapshots"][0]["provenance"]["retrieved_at_utc"] = "2025-06-01T22:01:00Z"
    assert "PREGAME_SNAPSHOT_RETRIEVED_AFTER_AS_OF_UTC" in reasons(build_historical_pregame_training_ledger(data))
    data = ledger_input(); data["pregame_snapshots"][0]["available_at_utc"] = "2025-06-01T22:01:00Z"
    assert "PREGAME_SNAPSHOT_AVAILABLE_AFTER_AS_OF_UTC" in reasons(build_historical_pregame_training_ledger(data))
    data = ledger_input(); data["pregame_snapshots"][0]["as_of_utc"] = "2025-06-01T23:00:00Z"
    assert "PREDICTION_CUTOFF_NOT_STRICTLY_PREGAME" in reasons(build_historical_pregame_training_ledger(data))
    data = ledger_input(); data["final_box_scores"] = []
    assert "ABSENT_FINAL_OFFICIAL_LABEL" in reasons(build_historical_pregame_training_ledger(data))
    data = ledger_input(); data["pregame_snapshots"][0]["provenance"]["raw_response_hash"] = "bad"
    assert "MALFORMED_SOURCE_EVIDENCE" in reasons(build_historical_pregame_training_ledger(data))


def test_final_only_players_mismatches_postgame_starter_and_source_health_are_exclusions():
    data = ledger_input(); data["final_box_scores"][0]["payload"]["teams"]["away"]["players"]["ID999"] = {"person": {"id": 999}, "stats": {"batting": {"hits": 1}}}; data["final_box_scores"][0]["payload"]["teams"]["away"]["batters"].append(999)
    data["pregame_snapshots"][0]["starter_evidence"]["home"].append({"kind": "actual", "player_mlbam_id": 802, "available_at_utc": "2025-06-02T01:00:00Z"})
    result = build_historical_pregame_training_ledger(data)
    assert {"FINAL_HITTER_NOT_IN_PREGAME_CONFIRMED_LINEUP", "POSTGAME_ACTUAL_STARTER_BACKFILL"} <= reasons(result)
    data = ledger_input(); data["pregame_snapshots"][0]["payload"]["liveData"]["boxscore"]["teams"]["away"] = team(list(range(101, 110)), 11, "AWY", mismatch=True)
    assert "CANONICAL_PLAYER_ID_MISMATCH" in reasons(build_historical_pregame_training_ledger(data))
    for failure, reason in (("RETRYABLE", "RETRYABLE_SOURCE_EVIDENCE"), ("TERMINAL", "TERMINAL_SOURCE_EVIDENCE")):
        data = ledger_input(); data["final_box_scores"][0]["provenance"]["source_failure_status"] = failure
        assert reason in reasons(build_historical_pregame_training_ledger(data))


def test_final_label_is_outcome_only_and_game_pk_or_stale_evidence_fails_closed():
    result = build_historical_pregame_training_ledger(ledger_input())
    assert result["rows"][0]["final_label_provenance"]["retrieved_at_utc"] == "2025-06-02T01:00:00Z"
    assert result["rows"][0]["as_of_utc"] < result["rows"][0]["scheduled_first_pitch_utc"]
    data = ledger_input(); data["final_box_scores"][0]["payload"]["gamePk"] = 900002
    assert "MISMATCHED_GAME_PK" in reasons(build_historical_pregame_training_ledger(data))
    data = ledger_input(); data["pregame_snapshots"][0]["provenance"]["source_freshness_status"] = "STALE"
    assert "STALE_SOURCE_EVIDENCE" in reasons(build_historical_pregame_training_ledger(data))


def test_order_schema_and_no_network_or_display_name_joins(monkeypatch):
    monkeypatch.setattr(socket, "create_connection", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("network")))
    result = build_historical_pregame_training_ledger(ledger_input())
    schema = json.loads((ROOT / "schemas" / "historical_pregame_training_ledger.schema.json").read_text())
    jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker()).validate(result)
    assert result["rows"] == sorted(result["rows"], key=lambda row: (row["game_pk"], row["confirmed_lineup_side"], row["batting_order_slot"], row["player_mlbam_id"]))
    source = (ROOT / "src" / "hitterdna" / "historical_pregame_training_ledger.py").read_text()
    assert "player_name" not in source and "requests" not in {node.names[0].name for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Import)}
