from dataclasses import replace
import json
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from hitterdna.park_weather import (
    ParkWeatherSnapshot,
    WindNormalizationResult,
    evaluate_park_factor_status,
    normalize_wind,
)


FIXTURES = Path(__file__).parent / "fixtures" / "park_weather"
ROOT = Path(__file__).parents[1]


def load_fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text())


def baseline_snapshot() -> ParkWeatherSnapshot:
    return ParkWeatherSnapshot(**load_fixture("baseline.json"))


def schema_validator() -> jsonschema.Draft202012Validator:
    schema = json.loads((ROOT / "schemas" / "park_weather_snapshot.schema.json").read_text())
    return jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker())


def test_pure_outward_wind_produces_positive_outward_component() -> None:
    result = normalize_wind(baseline_snapshot())

    assert result.wind_normalization_status == "PASS"
    assert result.outward_wind_mph == pytest.approx(10.0)
    assert result.omission_reason is None


def test_pure_inward_wind_produces_negative_outward_component() -> None:
    snapshot = replace(baseline_snapshot(), wind_from_deg=0.0)

    result = normalize_wind(snapshot)

    assert result.wind_normalization_status == "PASS"
    assert result.outward_wind_mph == pytest.approx(-10.0)


def test_crosswind_produces_approximately_zero_outward_component() -> None:
    snapshot = replace(baseline_snapshot(), wind_from_deg=90.0)

    result = normalize_wind(snapshot)

    assert result.wind_normalization_status == "PASS"
    assert result.outward_wind_mph == pytest.approx(0.0, abs=1e-12)


def test_missing_wind_direction_is_omitted() -> None:
    result = normalize_wind(replace(baseline_snapshot(), wind_from_deg=None))

    assert result.wind_normalization_status == "OMITTED"
    assert result.outward_wind_mph is None
    assert result.omission_reason == "missing_or_invalid_wind_direction"


def test_missing_center_field_bearing_is_omitted() -> None:
    result = normalize_wind(replace(baseline_snapshot(), center_field_bearing_deg=None))

    assert result.wind_normalization_status == "OMITTED"
    assert result.omission_reason == "missing_or_invalid_center_field_bearing"


@pytest.mark.parametrize(
    ("field", "expected_reason"),
    [
        ("provider", "missing_or_invalid_source_provenance"),
        ("provider_location_key", "missing_or_invalid_source_provenance"),
        ("source_request", "missing_or_invalid_source_provenance"),
        ("raw_response_hash", "missing_or_invalid_source_provenance"),
        ("retrieved_at_utc", "missing_or_invalid_source_provenance"),
        ("valid_at_utc", "missing_or_invalid_valid_timestamp"),
    ],
)
def test_missing_source_provenance_or_valid_timestamp_is_omitted(
    field: str, expected_reason: str
) -> None:
    result = normalize_wind(replace(baseline_snapshot(), **{field: None}))

    assert result.wind_normalization_status == "OMITTED"
    assert result.omission_reason == expected_reason


def test_space_separated_valid_timestamp_is_omitted() -> None:
    snapshot = replace(baseline_snapshot(), valid_at_utc="2026-09-01 20:00:00+00:00")

    result = normalize_wind(snapshot)

    assert result == WindNormalizationResult(
        wind_normalization_status="OMITTED",
        outward_wind_mph=None,
        omission_reason="missing_or_invalid_valid_timestamp",
    )


def test_closed_retractable_roof_is_omitted() -> None:
    result = normalize_wind(replace(baseline_snapshot(), roof_state="closed"))

    assert result.wind_normalization_status == "OMITTED"
    assert result.omission_reason == "roof_closed"


def test_unknown_roof_state_is_omitted() -> None:
    result = normalize_wind(replace(baseline_snapshot(), roof_state="unknown"))

    assert result.wind_normalization_status == "OMITTED"
    assert result.omission_reason == "roof_unknown"


def test_sutter_health_park_is_unverified() -> None:
    fixture = load_fixture("park_exceptions.json")["sutter_health"]

    decision = evaluate_park_factor_status(**fixture)

    assert decision.park_factor_status == "UNVERIFIED"
    assert decision.reason == "sutter_health_unverified"


def test_rays_continuity_exception_is_preserved() -> None:
    fixture = load_fixture("park_exceptions.json")["rays_continuity"]

    decision = evaluate_park_factor_status(**fixture)

    assert decision.park_factor_status == "PASS"
    assert decision.rays_continuity_suspect is True


def test_valid_fixture_satisfies_schema() -> None:
    schema_validator().validate(load_fixture("baseline.json"))


def test_schema_rejects_negative_wind_speed() -> None:
    payload = load_fixture("baseline.json") | {"wind_speed_mph": -0.1}

    with pytest.raises(jsonschema.ValidationError):
        schema_validator().validate(payload)


@pytest.mark.parametrize("wind_from_deg", [-0.1, 360.0, 361.0])
def test_schema_rejects_wind_direction_outside_range(wind_from_deg: float) -> None:
    payload = load_fixture("baseline.json") | {"wind_from_deg": wind_from_deg}

    with pytest.raises(jsonschema.ValidationError):
        schema_validator().validate(payload)


def test_schema_rejects_invalid_roof_state() -> None:
    payload = load_fixture("baseline.json") | {"roof_state": "retractable"}

    with pytest.raises(jsonschema.ValidationError):
        schema_validator().validate(payload)


@pytest.mark.parametrize(
    "field",
    ["valid_at_utc", "retrieved_at_utc"],
)
def test_schema_rejects_malformed_timestamp(field: str) -> None:
    payload = load_fixture("baseline.json") | {field: "2030-07-04 19:05:00"}

    with pytest.raises(jsonschema.ValidationError):
        schema_validator().validate(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [("humidity", 55), ("unexpected", "not allowed")],
)
def test_schema_rejects_humidity_and_unknown_fields(field: str, value: Any) -> None:
    payload = load_fixture("baseline.json") | {field: value}

    with pytest.raises(jsonschema.ValidationError):
        schema_validator().validate(payload)
