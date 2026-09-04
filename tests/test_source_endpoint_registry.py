import copy
import json
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from hitterdna.source_endpoint_registry import (
    AUTOMATED_IDS,
    CANONICAL_KEYS,
    DENOMINATORS,
    ENTRY_FIELDS,
    FAILURE_STATUSES,
    FRESHNESS_STATUSES,
    RegistryValidationError,
    UNIVERSAL_PROVENANCE_FIELDS,
    automation_ready_entries,
    evaluate_statcast_search_response,
    load_source_endpoint_registry,
    validate_denominator_definition,
    validate_conditional_canonical_keys,
    validate_request_parameters,
    validate_source_health_statuses,
)


ROOT = Path(__file__).parents[1]
REGISTRY_PATH = ROOT / "config" / "source_endpoint_registry.json"
SCHEMA_PATH = ROOT / "schemas" / "source_endpoint_registry.schema.json"
FIXTURES = Path(__file__).parent / "fixtures" / "source_endpoint_registry"
EXPECTED_IDS = {
    "mlb_statsapi_schedule_by_date", "mlb_statsapi_live_game_feed", "mlb_statsapi_lineup_confirmation", "mlb_statsapi_probable_pitchers", "mlb_statsapi_player_identity", "mlb_statsapi_venue_identity", "mlb_statsapi_transactions_il_context", "mlb_statsapi_box_scores", "savant_expected_statistics", "savant_custom_leaderboards", "savant_pitch_arsenal_batter", "savant_statcast_search_csv", "savant_statcast_park_factors", "savant_bat_tracking", "savant_swing_path_attack_angle", "fangraphs_the_bat_x", "fangraphs_steamer", "fangraphs_atc", "fangraphs_depth_charts", "fangraphs_roster_resource", "deferred_weather_interface", "deferred_market_interface",
}


def document() -> dict[str, Any]:
    return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))


def write_document(tmp_path: Path, value: dict[str, Any]) -> Path:
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def entry(value: dict[str, Any], endpoint_id: str) -> dict[str, Any]:
    return next(item for item in value["entries"] if item["endpoint_id"] == endpoint_id)


def metadata(value: dict[str, Any], name: str) -> Any:
    return next(item["value"] for item in value["exception_policy"]["metadata"] if item["name"] == name)


def test_committed_registry_validates_against_draft_2020_12_schema() -> None:
    jsonschema.Draft202012Validator(json.loads(SCHEMA_PATH.read_text())).validate(document())
    assert len(load_source_endpoint_registry()) == 22


def test_entries_are_exactly_the_seventeen_contract_fields_and_inventory_is_complete() -> None:
    entries = document()["entries"]
    assert {item["endpoint_id"] for item in entries} == EXPECTED_IDS
    assert all(set(item) == ENTRY_FIELDS for item in entries)


def test_capability_states_are_distinct_and_automation_ready_is_deliberate() -> None:
    entries = load_source_endpoint_registry()
    assert {item.implementation_status for item in entries} == {"AUTOMATED", "CONTRACT_ONLY", "NON_AUTOMATED", "DEFERRED"}
    assert {item.endpoint_id for item in automation_ready_entries()} == AUTOMATED_IDS
    assert all(item.implementation_status == "AUTOMATED" for item in automation_ready_entries())


def test_universal_provenance_canonical_keys_and_denominators_are_explicit() -> None:
    for item in document()["entries"]:
        assert set(item["provenance_fields"]) == UNIVERSAL_PROVENANCE_FIELDS
        assert all(item["provenance_fields"].values())
        assert set(item["canonical_keys"]["required"]).issubset(CANONICAL_KEYS)
        assert all(set(extra) == {"key", "justification"} and extra["justification"] for extra in item["canonical_keys"]["supplemental"])
        assert set(item["parameter_contract"]["denominator_definitions"]).issubset(DENOMINATORS)


def test_source_health_statuses_are_separate_exact_vocabularies() -> None:
    assert FRESHNESS_STATUSES == {"PASS", "STALE", "UNVERIFIED"}
    assert FAILURE_STATUSES == {"PASS", "RETRYABLE", "TERMINAL", "FAIL", "UNVERIFIED"}
    validate_source_health_statuses("STALE", "RETRYABLE")
    with pytest.raises(RegistryValidationError):
        validate_source_health_statuses("pass", "PASS")
    with pytest.raises(RegistryValidationError):
        validate_source_health_statuses("PASS", "UNKNOWN")


@pytest.mark.parametrize("denominator", DENOMINATORS)
def test_denominators_are_exact_and_cannot_be_silently_substituted(denominator: str) -> None:
    validate_denominator_definition(denominator, denominator)
    different = next(value for value in DENOMINATORS if value != denominator)
    with pytest.raises(RegistryValidationError):
        validate_denominator_definition(denominator, different)


def test_statcast_search_is_capped_chunked_and_requires_subdivision_at_cap() -> None:
    value = entry(document(), "savant_statcast_search_csv")
    assert value["row_limit"] == 30000
    assert value["chunking_contract"]["max_calendar_days"] == 5
    assert value["chunking_contract"]["inclusive_start_date"] is True
    assert value["chunking_contract"]["inclusive_end_date"] is True
    result = evaluate_statcast_search_response(json.loads((FIXTURES / "statcast_search_at_cap.json").read_text())["returned_row_count"])
    assert result.promotion_allowed is False and result.subdivision_required is True


def test_machine_readable_request_parameters_fail_closed() -> None:
    schedule = next(item for item in load_source_endpoint_registry() if item.endpoint_id == "mlb_statsapi_schedule_by_date")
    validate_request_parameters(schedule, {}, {"sportId": 1, "date": "2026-09-04", "hydrate": "probablePitcher,venue"})
    for query in ({"sportId": 2, "date": "x", "hydrate": "probablePitcher,venue"}, {"sportId": 1, "date": "x", "hydrate": "probablePitcher,venue", "unknown": "x"}):
        with pytest.raises(RegistryValidationError): validate_request_parameters(schedule, {}, query)
    venue = next(item for item in load_source_endpoint_registry() if item.endpoint_id == "mlb_statsapi_venue_identity")
    for path in ({}, {"venue_mlbam_id": 0}, {"venue_mlbam_id": "1"}):
        with pytest.raises(RegistryValidationError): validate_request_parameters(venue, path, {})


def test_documented_external_parameter_names_map_to_canonical_context_templates() -> None:
    registry = document()
    expected = {
        "mlb_statsapi_schedule_by_date": {"date": "{game_date}"},
        "savant_expected_statistics": {"year": "{season}"},
        "mlb_statsapi_transactions_il_context": {"startDate": "{start_date}", "endDate": "{end_date}"},
    }
    for endpoint_id, pairs in expected.items():
        parameters = entry(registry, endpoint_id)["parameter_contract"]["query_parameters"]
        templates = {item["name"]: item["constraint"]["value"] for item in parameters if item["constraint"]["kind"] == "template"}
        assert templates == pairs
    variants = entry(registry, "mlb_statsapi_probable_pitchers")["parameter_contract"]["request_variants"]
    schedule_variant = next(item for item in variants if item["selection_rule"] == "schedule_first")
    templates = {item["name"]: item["constraint"]["value"] for item in schedule_variant["query_parameters"] if item["constraint"]["kind"] == "template"}
    assert templates == {"date": "{game_date}"}


def test_variants_transactions_and_conditional_bat_tracking_keys(tmp_path: Path) -> None:
    registry = document()
    for endpoint_id in ("mlb_statsapi_probable_pitchers", "mlb_statsapi_player_identity"):
        value = entry(registry, endpoint_id)
        assert value["endpoint_template"] is None and len(value["parameter_contract"]["request_variants"]) == 2
    transactions = next(item for item in load_source_endpoint_registry() if item.endpoint_id == "mlb_statsapi_transactions_il_context")
    validate_request_parameters(transactions, {}, {"sportId": 1, "startDate": "x", "endDate": "x", "playerId": 1})
    validate_request_parameters(transactions, {}, {"sportId": 1, "startDate": "x", "endDate": "x", "teamId": 1})
    entries = load_source_endpoint_registry()
    for endpoint_id in ("savant_bat_tracking", "savant_swing_path_attack_angle"):
        tracking = next(item for item in entries if item.endpoint_id == endpoint_id)
        validate_conditional_canonical_keys(tracking, date_bounded=False, game_level=False, response_keys={"player_mlbam_id", "season"})
        validate_conditional_canonical_keys(tracking, date_bounded=True, game_level=False, response_keys={"player_mlbam_id", "season", "game_date"})
        with pytest.raises(RegistryValidationError): validate_conditional_canonical_keys(tracking, date_bounded=True, game_level=False, response_keys={"player_mlbam_id", "season"})
    broken = copy.deepcopy(registry); del entry(broken, "mlb_statsapi_probable_pitchers")["parameter_contract"]["request_variants"][0]["method"]
    with pytest.raises(RegistryValidationError): load_source_endpoint_registry(write_document(tmp_path, broken))


def test_bat_tracking_park_factors_fangraphs_and_deferred_policy_metadata() -> None:
    registry = document()
    for endpoint_id in ("savant_bat_tracking", "savant_swing_path_attack_angle"):
        value = entry(registry, endpoint_id)
        assert metadata(value, "availability_beginning") == "second_half_2023"
        assert metadata(value, "no_multiyear_trends") is True
        assert metadata(value, "squared_up_descriptive_pending_calibration") is True
    park = entry(registry, "savant_statcast_park_factors")
    assert metadata(park, "index_baseline") == "100_is_average"
    assert metadata(park, "sutter_health_park_status") == "UNVERIFIED"
    assert metadata(park, "rays_continuity_suspect") is True
    for value in registry["entries"]:
        if value["endpoint_id"].startswith("fangraphs_"):
            assert value["implementation_status"] == "NON_AUTOMATED" and value["endpoint_template"] is None
    for endpoint_id in ("deferred_weather_interface", "deferred_market_interface"):
        value = entry(registry, endpoint_id)
        assert value["implementation_status"] == "DEFERRED" and value["method"] == "NONE" and value["endpoint_template"] is None


@pytest.mark.parametrize("case", json.loads((FIXTURES / "invalid_cases.json").read_text()).keys())
def test_invalid_fixtures_fail_closed(tmp_path: Path, case: str) -> None:
    value = copy.deepcopy(document())
    if case == "missing_provenance_field":
        del entry(value, "mlb_statsapi_schedule_by_date")["provenance_fields"]["raw_response_hash"]
    elif case == "unapproved_top_level_entry_field":
        entry(value, "mlb_statsapi_schedule_by_date")["unreviewed"] = True
    elif case == "missing_canonical_keys":
        entry(value, "mlb_statsapi_schedule_by_date")["canonical_keys"]["required"] = []
    elif case == "invented_numeric_freshness_ttl":
        entry(value, "mlb_statsapi_schedule_by_date")["freshness_ttl_minutes"] = 60
    elif case == "invalid_response_format":
        entry(value, "mlb_statsapi_schedule_by_date")["response_format"] = {"format": "CSV", "response_type": "OBJECT"}
    elif case == "row_limit_or_chunking_contradiction":
        entry(value, "savant_statcast_search_csv")["chunking_contract"]["max_calendar_days"] = 6
    elif case == "automated_non_automated_source":
        entry(value, "fangraphs_atc")["implementation_status"] = "AUTOMATED"
    else:
        fixture = json.loads((FIXTURES / "invalid_cases.json").read_text())[case]
        with pytest.raises(RegistryValidationError):
            validate_source_health_statuses(fixture["freshness"], fixture["failure"])
        return
    with pytest.raises(RegistryValidationError):
        load_source_endpoint_registry(write_document(tmp_path, value))
