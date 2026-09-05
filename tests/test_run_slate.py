import json
from copy import deepcopy
from pathlib import Path
import subprocess
import sys

import pytest
from jsonschema import Draft202012Validator, FormatChecker, ValidationError

import hitterdna.run_slate as run_slate
from hitterdna.run_slate import SlateRunValidationError, build_slate_run, main, write_slate_run


ROOT = Path(__file__).parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "slate_run" / "eligible.json"
ARTIFACT_FILENAMES = {
    "run_manifest.json", "slate_context.json", "source_observations.jsonl",
    "confirmed_lineups.json", "starter_context.json", "expected_statistics.json",
    "filter_table.json", "discovery_queue.json", "exclusions.json",
    "validation_report.json",
}


def fixture(tmp_path: Path, mutate=None) -> Path:
    data = json.loads(FIXTURE.read_text())
    data["lineup_payloads"] = {
        game_pk: str((FIXTURE.parent / path).resolve())
        for game_pk, path in data["lineup_payloads"].items()
    }
    data["expected_statistics"]["stabilization_policy_path"] = str(
        (FIXTURE.parent / data["expected_statistics"]["stabilization_policy_path"]).resolve()
    )
    if mutate:
        mutate(data)
    path = tmp_path / "fixture.json"
    path.write_text(json.dumps(data))
    return path


def test_writes_all_artifacts_and_confirmed_candidate_advances(tmp_path: Path) -> None:
    output = tmp_path / "slate"
    write_slate_run(build_slate_run("2030-01-01", fixture(tmp_path)), output)
    expected = ARTIFACT_FILENAMES
    assert {p.name for p in output.iterdir()} == expected
    schema = json.loads((ROOT / "schemas" / "slate_run_artifact.schema.json").read_text())
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    for filename in expected - {"source_observations.jsonl"}:
        assert not list(validator.iter_errors(json.loads((output / filename).read_text())))
    source_validator = Draft202012Validator(json.loads((ROOT / "schemas" / "source_observation.schema.json").read_text()))
    assert all(not list(source_validator.iter_errors(json.loads(line))) for line in (output / "source_observations.jsonl").read_text().splitlines())
    assert json.loads((output / "discovery_queue.json").read_text())["data"]["records"][0]["player_mlbam_id"] == 101


def test_missing_and_stale_lineups_fail_closed(tmp_path: Path) -> None:
    missing = fixture(tmp_path, lambda d: d["lineup_payloads"].clear())
    assert build_slate_run("2030-01-01", missing)["exclusions"]["data"]["records"][0]["status"] == "UNVERIFIED"
    stale = fixture(tmp_path, lambda d: d["provenance"]["mlb_statsapi_lineup_confirmation"].update(source_freshness_status="STALE"))
    artifact = build_slate_run("2030-01-01", stale)
    assert artifact["confirmed_lineups"]["data"]["games"][0]["away"] == []
    assert artifact["exclusions"]["data"]["records"][0]["status"] == "STALE"


@pytest.mark.parametrize(
    ("freshness", "failure", "expected"),
    [
        ("STALE", "TERMINAL", "TERMINAL"),
        ("STALE", "RETRYABLE", "RETRYABLE"),
        ("UNVERIFIED", "FAIL", "UNVERIFIED"),
        ("STALE", "FAIL", "STALE"),
        ("PASS", "PASS", "PASS"),
    ],
)
def test_provenance_health_uses_declared_status_precedence(
    freshness: str, failure: str, expected: str,
) -> None:
    assert run_slate._provenance_health({
        "source_freshness_status": freshness,
        "source_failure_status": failure,
    }) == expected


def test_stale_schedule_source_creates_no_usable_context_or_discovery_records(tmp_path: Path) -> None:
    source = fixture(tmp_path, lambda d: d["provenance"]["mlb_statsapi_schedule_by_date"].update(source_freshness_status="STALE"))
    artifact = build_slate_run("2030-01-01", source)
    assert artifact["slate_context"]["data"]["games"] == []
    assert artifact["discovery_queue"]["data"]["records"] == []
    assert artifact["validation_report"]["data"]["status"] == "STALE"
    assert artifact["validation_report"]["status"] == "STALE"
    output = tmp_path / "degraded-slate"
    write_slate_run(artifact, output)
    assert {path.name for path in output.iterdir()} == ARTIFACT_FILENAMES


def test_retryable_schedule_source_creates_no_discovery_records(tmp_path: Path) -> None:
    source = fixture(tmp_path, lambda d: d["provenance"]["mlb_statsapi_schedule_by_date"].update(source_failure_status="RETRYABLE"))
    artifact = build_slate_run("2030-01-01", source)
    assert artifact["slate_context"]["data"]["games"] == []
    assert artifact["discovery_queue"]["data"]["records"] == []
    assert artifact["validation_report"]["data"]["status"] == "RETRYABLE"
    assert artifact["validation_report"]["status"] == "RETRYABLE"


@pytest.mark.parametrize(
    ("health_update", "expected_status"),
    [
        ({"source_freshness_status": "STALE"}, "STALE"),
        ({"source_failure_status": "RETRYABLE"}, "RETRYABLE"),
        ({"source_failure_status": "FAIL"}, "FAIL"),
        ({"source_freshness_status": "UNVERIFIED"}, "UNVERIFIED"),
    ],
)
def test_unhealthy_savant_is_not_parsed_or_used_and_excludes_confirmed_hitters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, health_update: dict[str, str], expected_status: str,
) -> None:
    def fail_if_parsed(*args: object, **kwargs: object) -> object:
        raise AssertionError("unhealthy Savant payload must not be parsed")

    monkeypatch.setattr(run_slate, "parse_expected_stats_payload", fail_if_parsed)
    source = fixture(tmp_path, lambda d: d["provenance"]["savant_expected_statistics"].update(health_update))
    artifact = build_slate_run("2030-01-01", source)
    assert artifact["expected_statistics"]["data"]["rows"] == []
    assert artifact["expected_statistics"]["status"] == expected_status
    assert artifact["discovery_queue"]["data"]["records"] == []
    assert artifact["filter_table"]["data"]["candidates"]
    assert all(record["status"] == "UNVERIFIED" for record in artifact["exclusions"]["data"]["records"])
    assert artifact["validation_report"]["data"]["status"] == expected_status


def test_stale_lineup_source_degrades_lineup_and_overall_status(tmp_path: Path) -> None:
    source = fixture(tmp_path, lambda d: d["provenance"]["mlb_statsapi_lineup_confirmation"].update(source_freshness_status="STALE"))
    artifact = build_slate_run("2030-01-01", source)
    assert artifact["confirmed_lineups"]["status"] == "STALE"
    assert artifact["validation_report"]["status"] == "STALE"
    assert artifact["validation_report"]["data"]["status"] == "STALE"


def test_normal_eligible_fixture_has_pass_statuses_and_advances_known_player(tmp_path: Path) -> None:
    artifact = build_slate_run("2030-01-01", fixture(tmp_path))
    assert {name: value["status"] for name, value in artifact.items()} == {
        name: "PASS" for name in artifact
    }
    assert artifact["validation_report"]["data"]["status"] == "PASS"
    assert artifact["discovery_queue"]["data"]["records"][0]["player_mlbam_id"] == 101


def test_terminal_required_source_returns_terminal_package_without_candidates(tmp_path: Path) -> None:
    source = fixture(tmp_path, lambda d: d["provenance"]["savant_expected_statistics"].update(source_failure_status="TERMINAL"))
    artifact = build_slate_run("2030-01-01", source)
    assert {value["status"] for value in artifact.values()} == {"TERMINAL"}
    assert artifact["slate_context"]["data"]["games"] == []
    assert artifact["discovery_queue"]["data"]["records"] == []


def test_missing_schema_error_names_schema_path_and_prevents_promotion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifacts = build_slate_run("2030-01-01", fixture(tmp_path))
    missing_module_path = tmp_path / "missing-checkout" / "src" / "hitterdna" / "run_slate.py"
    monkeypatch.setattr(run_slate, "__file__", str(missing_module_path))
    output = tmp_path / "not-promoted"
    with pytest.raises(SlateRunValidationError) as error:
        write_slate_run(artifacts, output)
    message = str(error.value)
    expected_path = (missing_module_path.resolve().parents[2] / "schemas" / "slate_run_artifact.schema.json").resolve()
    assert "slate_run_artifact.schema.json" in message
    assert str(expected_path) in message
    assert "Slate Run v0 requires a source checkout containing the schemas directory" in message
    assert not output.exists()


def test_actual_starter_wins_and_missing_expected_excludes(tmp_path: Path) -> None:
    artifact = build_slate_run("2030-01-01", fixture(tmp_path))
    away = artifact["starter_context"]["data"]["games"][0]["starters"][0]
    actual = next(record for record in away["evidence"] if record["kind"] == "actual")
    assert {record["kind"] for record in away["evidence"]} == {"actual", "probable"}
    assert actual["player_mlbam_id"] == 503
    assert away["selected"]["kind"] == "actual"
    assert away["selected"]["player_mlbam_id"] == 503
    assert any(r["player_mlbam_id"] == 102 and r["status"] == "UNVERIFIED" for r in artifact["exclusions"]["data"]["records"])


def test_bad_provenance_has_no_partial_package(tmp_path: Path) -> None:
    source = fixture(tmp_path, lambda d: d["provenance"]["savant_expected_statistics"].pop("raw_response_hash"))
    with pytest.raises(SlateRunValidationError):
        build_slate_run("2030-01-01", source)
    assert not (tmp_path / "slate").exists()


@pytest.mark.parametrize(
    ("freshness", "failure"),
    [("STALE", "PASS"), ("PASS", "RETRYABLE"), ("PASS", "TERMINAL"), ("PASS", "FAIL")],
)
def test_unhealthy_actual_never_overrides_fresh_probable(
    tmp_path: Path, freshness: str, failure: str,
) -> None:
    def mutate(data: dict) -> None:
        data["starter_evidence"][1].update(
            source_freshness_status=freshness, source_failure_status=failure,
        )

    away = build_slate_run("2030-01-01", fixture(tmp_path, mutate))["starter_context"]["data"]["games"][0]["starters"][0]
    assert away["selected"]["player_mlbam_id"] == 501
    assert away["evidence"][1]["source_health_status"] in {"STALE", "RETRYABLE", "TERMINAL", "FAIL"}


def test_no_valid_starter_evidence_reports_deterministic_status(tmp_path: Path) -> None:
    def mutate(data: dict) -> None:
        for record in data["starter_evidence"]:
            record.update(source_freshness_status="STALE", source_failure_status="PASS")

    away = build_slate_run("2030-01-01", fixture(tmp_path, mutate))["starter_context"]["data"]["games"][0]["starters"][0]
    assert away["selected"] is None
    assert away["status"] == "STALE"


def test_contract_only_sources_are_reported_and_bytes_are_deterministic(tmp_path: Path) -> None:
    first, second = tmp_path / "one", tmp_path / "two"
    artifacts = build_slate_run("2030-01-01", fixture(tmp_path))
    write_slate_run(artifacts, first); write_slate_run(artifacts, second)
    assert {p.name: p.read_bytes() for p in first.iterdir()} == {p.name: p.read_bytes() for p in second.iterdir()}
    unavailable = artifacts["run_manifest"]["data"]["unavailable_sources"]
    assert any(row["implementation_status"] == "CONTRACT_ONLY" for row in unavailable)
    rows = [json.loads(line) for line in (first / "source_observations.jsonl").read_text().splitlines()]
    assert len(rows) == 4
    assert {row["endpoint_id"] for row in rows} == {
        "mlb_statsapi_schedule_by_date", "mlb_statsapi_lineup_confirmation",
        "mlb_statsapi_probable_pitchers", "savant_expected_statistics",
    }
    provenance_fields = {
        "source", "endpoint_or_url", "submitted_parameters", "retrieved_at_utc",
        "raw_response_hash", "row_count_when_relevant", "canonical_keys",
        "source_freshness_status", "source_failure_status",
    }
    for row in rows:
        assert set(row) == {"endpoint_id", "provenance", "retrieval_status"}
        assert set(row["provenance"]) == provenance_fields
        assert row["retrieval_status"] == "PASS"


def test_schema_invalid_artifact_and_source_observation_fail_before_promotion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    malformed_artifact = build_slate_run("2030-01-01", fixture(tmp_path))
    malformed_artifact["run_manifest"]["unexpected"] = True
    with pytest.raises(SlateRunValidationError):
        write_slate_run(malformed_artifact, tmp_path / "bad-artifact")
    assert not (tmp_path / "bad-artifact").exists()

    artifacts = build_slate_run("2030-01-01", fixture(tmp_path))
    monkeypatch.setattr(run_slate, "_contributing_source_observations", lambda _: [{"endpoint_id": "bad"}])
    with pytest.raises(SlateRunValidationError):
        write_slate_run(artifacts, tmp_path / "bad-source")
    assert not (tmp_path / "bad-source").exists()


@pytest.mark.parametrize(
    ("artifact_type", "field", "value"),
    [
        ("run_manifest", "artifact_type", "unknown_artifact"),
        ("run_manifest", "generated_at_utc", 0),
        ("run_manifest", "provenance", {}),
        ("run_manifest", "data.network_mode", None),
        ("run_manifest", "data.network_mode", "enabled"),
        ("slate_context", "data.games", None),
        ("validation_report", "data.source_contracts", None),
        ("run_manifest", "data.unexpected", True),
    ],
)
def test_artifact_schema_rejects_invalid_envelope_and_data(
    tmp_path: Path, artifact_type: str, field: str, value: object,
) -> None:
    artifact = deepcopy(build_slate_run("2030-01-01", fixture(tmp_path))[artifact_type])
    if field.startswith("data."):
        key = field.removeprefix("data.")
        if value is None:
            artifact["data"].pop(key)
        else:
            artifact["data"][key] = value
    else:
        artifact[field] = value
    schema = json.loads((ROOT / "schemas" / "slate_run_artifact.schema.json").read_text())
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    with pytest.raises(ValidationError):
        validator.validate(artifact)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("endpoint_id", " "),
        ("source", ""),
        ("endpoint_or_url", ""),
        ("retrieved_at_utc", "not-a-date-time"),
        ("raw_response_hash", ""),
        ("row_count_when_relevant", "1"),
        ("row_count_when_relevant", -1),
        ("row_count_when_relevant", 1.5),
    ],
)
def test_source_observation_schema_rejects_invalid_provenance_fields(
    tmp_path: Path, field: str, value: object,
) -> None:
    artifacts = build_slate_run("2030-01-01", fixture(tmp_path))
    row = deepcopy(run_slate._contributing_source_observations(artifacts["run_manifest"]["provenance"])[0])
    if field == "endpoint_id":
        row[field] = value
    else:
        row["provenance"][field] = value
    schema = json.loads((ROOT / "schemas" / "source_observation.schema.json").read_text())
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    with pytest.raises(ValidationError):
        validator.validate(row)


def test_cli_fixture_mode_succeeds(tmp_path: Path) -> None:
    assert main(["--date", "2030-01-01", "--fixtures", str(fixture(tmp_path)), "--output", str(tmp_path / "output")]) == 0


def test_module_execution_writes_deterministic_fixture_package(tmp_path: Path) -> None:
    fixture_path = fixture(tmp_path)
    outputs = (tmp_path / "first", tmp_path / "second")
    for output_directory in outputs:
        completed = subprocess.run(
            [
                sys.executable, "-m", "hitterdna.run_slate",
                "--date", "2030-01-01",
                "--output", str(output_directory),
                "--fixtures", str(fixture_path),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
        assert {path.name for path in output_directory.iterdir()} == ARTIFACT_FILENAMES
        json.loads((output_directory / "run_manifest.json").read_text())
        json.loads((output_directory / "validation_report.json").read_text())
    assert {path.name: path.read_bytes() for path in outputs[0].iterdir()} == {
        path.name: path.read_bytes() for path in outputs[1].iterdir()
    }


def test_module_execution_atomically_replaces_same_output(tmp_path: Path) -> None:
    fixture_path = fixture(tmp_path)
    output_directory = tmp_path / "same-output"
    command = [
        sys.executable, "-m", "hitterdna.run_slate",
        "--date", "2030-01-01",
        "--output", str(output_directory),
        "--fixtures", str(fixture_path),
    ]
    first = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
    assert first.returncode == 0, first.stderr
    first_bytes = {path.name: path.read_bytes() for path in output_directory.iterdir()}

    second = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
    assert second.returncode == 0, second.stderr
    assert {path.name for path in output_directory.iterdir()} == ARTIFACT_FILENAMES
    assert {path.name: path.read_bytes() for path in output_directory.iterdir()} == first_bytes
    assert not (tmp_path / ".same-output.previous").exists()


def test_module_execution_resolves_fixture_paths_from_external_cwd(tmp_path: Path) -> None:
    external_cwd = tmp_path / "external-cwd"
    external_cwd.mkdir()
    output_directory = tmp_path / "output"
    completed = subprocess.run(
        [
            sys.executable, "-m", "hitterdna.run_slate",
            "--date", "2030-01-01",
            "--output", str(output_directory.resolve()),
            "--fixtures", str(FIXTURE.resolve()),
        ],
        cwd=external_cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert {path.name for path in output_directory.iterdir()} == ARTIFACT_FILENAMES
