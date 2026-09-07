import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from hitterdna.prepare_slate_fixture import SlateFixturePreparationError, prepare_slate_fixture
from hitterdna.run_slate import build_slate_run, write_slate_run


ROOT = Path(__file__).parents[1]


def _raw_schedule() -> dict:
    return {
        "copyright": "fixture",
        "totalItems": 1,
        "totalGames": 1,
        "dates": [{
            "date": "2026-09-07",
            "games": [{
                "gamePk": 123456,
                "gameDate": "2026-09-07T23:10:00Z",
                "status": {"detailedState": "Scheduled"},
                "venue": {"id": 1, "name": "Fixture Park"},
                "teams": {
                    "away": {"team": {"id": 1, "name": "Away", "abbreviation": "AWY"}},
                    "home": {"team": {"id": 2, "name": "Home", "abbreviation": "HOM"}},
                },
            }],
        }],
    }


def _write_raw_schedule(tmp_path: Path, payload: object | None = None) -> Path:
    path = tmp_path / "schedule-raw.json"
    path.write_text(json.dumps(payload if payload is not None else _raw_schedule()), encoding="utf-8")
    return path


def test_raw_schedule_becomes_auditable_schedule_only_fixture(tmp_path: Path) -> None:
    raw_path = _write_raw_schedule(tmp_path)
    fixture = prepare_slate_fixture("2026-09-07", raw_path, retrieved_at_utc="2026-09-06T12:00:00Z")
    provenance = fixture["provenance"]["mlb_statsapi_schedule_by_date"]
    assert fixture["schedule"] == _raw_schedule()
    assert provenance["raw_response_hash"] == hashlib.sha256(raw_path.read_bytes()).hexdigest()
    assert provenance["submitted_parameters"] == {"sportId": 1, "date": "2026-09-07"}
    assert provenance["canonical_keys"] == {"game_pk": [123456]}
    assert provenance["row_count_when_relevant"] == 1
    assert provenance["source_freshness_status"] == provenance["source_failure_status"] == "PASS"
    assert provenance["endpoint_or_url"].startswith("local operator-supplied MLB StatsAPI schedule snapshot")
    assert set(fixture["unavailable_source_inputs"]) == {
        "mlb_statsapi_lineup_confirmation", "mlb_statsapi_probable_pitchers",
        "savant_expected_statistics",
    }
    assert all(value["status"] == "UNVERIFIED" for value in fixture["unavailable_source_inputs"].values())
    policy = fixture["filter"]
    assert policy["policy_source_reference"] == "docs/filter-thresholds.md"
    assert policy["policy_content_sha256"] == hashlib.sha256(
        (ROOT / "docs" / "filter-thresholds.md").read_bytes()
    ).hexdigest()
    assert policy["policy_loaded_at_utc"] == "2026-09-06T12:00:00Z"
    assert policy["policy_version"] == "filter-thresholds-v0.1"


def test_preparer_rejects_date_mismatch_and_malformed_schedule_json(tmp_path: Path) -> None:
    raw_path = _write_raw_schedule(tmp_path)
    with pytest.raises(SlateFixturePreparationError, match="does not include requested date 2026-09-08"):
        prepare_slate_fixture("2026-09-08", raw_path)
    malformed = tmp_path / "malformed.json"
    malformed.write_text("{not json", encoding="utf-8")
    with pytest.raises(SlateFixturePreparationError, match="not valid JSON"):
        prepare_slate_fixture("2026-09-07", malformed)


def test_preparation_is_deterministic_when_timestamp_is_injected(tmp_path: Path) -> None:
    raw_path = _write_raw_schedule(tmp_path)
    first = prepare_slate_fixture("2026-09-07", raw_path, retrieved_at_utc="2026-09-06T12:00:00Z")
    second = prepare_slate_fixture("2026-09-07", raw_path, retrieved_at_utc="2026-09-06T12:00:00Z")
    assert first == second


def test_schedule_only_fixture_runs_to_a_schema_valid_degraded_package(tmp_path: Path) -> None:
    fixture_path = tmp_path / "schedule-only.json"
    fixture_path.write_text(json.dumps(prepare_slate_fixture(
        "2026-09-07", _write_raw_schedule(tmp_path), retrieved_at_utc="2026-09-06T12:00:00Z",
    )), encoding="utf-8")
    output = tmp_path / "slate"
    write_slate_run(build_slate_run("2026-09-07", fixture_path), output)
    schema = Draft202012Validator(
        json.loads((ROOT / "schemas" / "slate_run_artifact.schema.json").read_text()),
        format_checker=FormatChecker(),
    )
    for path in output.glob("*.json"):
        assert not list(schema.iter_errors(json.loads(path.read_text()))), path.name
    assert json.loads((output / "slate_context.json").read_text())["data"]["games"]
    assert json.loads((output / "confirmed_lineups.json").read_text())["data"]["games"][0]["away"] == []
    assert json.loads((output / "expected_statistics.json").read_text())["data"]["rows"] == []
    assert json.loads((output / "filter_table.json").read_text())["data"]["candidates"] == []
    assert json.loads((output / "discovery_queue.json").read_text())["data"]["records"] == []
    exclusions = json.loads((output / "exclusions.json").read_text())["data"]["records"]
    assert all(record["status"] == "UNVERIFIED" for record in exclusions)
    assert {record["team_side"] for record in exclusions if "team_side" in record} == {"away", "home"}
    assert {record["source_input"] for record in exclusions if "source_input" in record} >= {
        "mlb_statsapi_lineup_confirmation", "savant_expected_statistics",
    }
    report = json.loads((output / "validation_report.json").read_text())
    assert report["status"] == "UNVERIFIED"
    assert {row["endpoint_id"] for row in report["data"]["source_contracts"]} >= {
        "mlb_statsapi_lineup_confirmation", "savant_expected_statistics",
    }
    observations = [json.loads(line) for line in (output / "source_observations.jsonl").read_text().splitlines()]
    assert [row["endpoint_id"] for row in observations] == ["mlb_statsapi_schedule_by_date"]


def test_preparer_module_cli_writes_fixture_without_network(tmp_path: Path) -> None:
    raw_path = _write_raw_schedule(tmp_path)
    output = tmp_path / "fixture.json"
    completed = subprocess.run(
        [sys.executable, "-m", "hitterdna.prepare_slate_fixture", "--date", "2026-09-07",
         "--schedule", str(raw_path), "--output", str(output)],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(output.read_text())["schedule"] == _raw_schedule()
    # The preparation module imports only local parsing and normalization code;
    # this subprocess supplies no network dependency or request hook.
    source = (ROOT / "src" / "hitterdna" / "prepare_slate_fixture.py").read_text()
    assert not any(module in source for module in ("requests", "urllib", "socket", "http.client"))
