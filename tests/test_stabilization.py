import ast
from dataclasses import FrozenInstanceError, asdict
import json
from math import inf, nan
from pathlib import Path
from typing import Any
from unittest.mock import patch

import jsonschema
import pytest

from hitterdna.filter_table import MetricObservation
from hitterdna.stabilization import (
    StabilizationDecision,
    StabilizationRegistry,
    apply_stabilization_to_observation,
    evaluate_stabilization,
    load_stabilization_policy_file,
)


FIXTURES = Path(__file__).parent / "fixtures" / "stabilization"
ROOT = Path(__file__).parents[1]


def load_fixture(name: str):
    result = load_stabilization_policy_file(FIXTURES / name)
    assert result.load_status == "loaded", result.error_messages
    return result


def valid_registry() -> StabilizationRegistry:
    return StabilizationRegistry(load_fixture("valid.json").policies)


def fixture_observation() -> MetricObservation:
    return MetricObservation(
        metric_key="fixture_pa_metric",
        value=0.123,
        sample_type="pa",
        sample_n=12,
        source_url="https://example.invalid/metrics/fixture",
        retrieved_at_utc="2030-02-03T04:05:06Z",
        source_name="Fixture metric source",
        stabilization_status="unverified",
        notes="fixture notes",
    )


def test_valid_file_loads_immutable_policies_and_exact_resolution() -> None:
    result = load_fixture("valid.json")
    registry = StabilizationRegistry(result.policies)

    assert len(result.policies) == 4
    assert registry.resolve("fixture_pa_metric", "pa").policy_id == "fixture-generic-pa"  # type: ignore[union-attr]
    with pytest.raises(FrozenInstanceError):
        result.policies[0].metric_key = "changed"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        registry.policies = ()  # type: ignore[misc]


@pytest.mark.parametrize(
    "fixture_name",
    [
        "missing_top_level.json",
        "missing_policy_field.json",
        "extra_top_level.json",
        "extra_policy_field.json",
        "invalid_url_timestamp.json",
        "invalid_minimum.json",
        "invalid_window.json",
        "duplicate_identity.json",
    ],
)
def test_malformed_policy_files_fail_closed(fixture_name: str) -> None:
    result = load_stabilization_policy_file(FIXTURES / fixture_name)

    assert result.load_status == "malformed"
    assert result.policies == ()
    assert result.error_messages


def test_registry_rejects_duplicate_identical_policy_identity() -> None:
    policy = load_fixture("valid.json").policies[0]

    with pytest.raises(ValueError, match="duplicate"):
        StabilizationRegistry((policy, policy))


def test_metric_and_sample_type_mismatches_are_unverified() -> None:
    registry = valid_registry()

    metric_mismatch = evaluate_stabilization("absent", "pa", 12, registry)
    sample_type_mismatch = evaluate_stabilization("fixture_pa_metric", "bip", 12, registry)

    assert metric_mismatch.status == "unverified"
    assert sample_type_mismatch.status == "unverified"


@pytest.mark.parametrize(
    ("sample_n", "expected_status"),
    [(13, "pass"), (12, "pass"), (11, "fail")],
)
def test_above_equal_and_below_policy_minimum(sample_n: int, expected_status: str) -> None:
    decision = evaluate_stabilization("fixture_pa_metric", "pa", sample_n, valid_registry())

    assert decision.status == expected_status
    assert decision.minimum_sample_value == 12


@pytest.mark.parametrize("sample_n", [None, "12", nan, inf, -1])
def test_missing_or_invalid_sample_values_are_unverified(sample_n: Any) -> None:
    decision = evaluate_stabilization("fixture_pa_metric", "pa", sample_n, valid_registry())

    assert decision.status == "unverified"
    assert decision.policy_id is None


def test_missing_sample_type_is_unverified() -> None:
    decision = evaluate_stabilization("fixture_pa_metric", None, 12, valid_registry())

    assert decision.status == "unverified"


def test_single_unbounded_policy_resolves_with_or_without_season() -> None:
    registry = valid_registry()

    assert registry.resolve("fixture_pa_metric", "pa").policy_id == "fixture-generic-pa"  # type: ignore[union-attr]
    assert registry.resolve("fixture_pa_metric", "pa", 2040).policy_id == "fixture-generic-pa"  # type: ignore[union-attr]


def test_season_windows_resolve_inside_range_and_not_outside() -> None:
    registry = valid_registry()

    assert registry.resolve("fixture_seasonal_metric", "pa", 2030).policy_id == "fixture-window-early"  # type: ignore[union-attr]
    assert registry.resolve("fixture_seasonal_metric", "pa", 2033).policy_id == "fixture-window-late"  # type: ignore[union-attr]
    assert registry.resolve("fixture_seasonal_metric", "pa", 2029) is None


def test_multiple_non_overlapping_windows_and_omitted_season_are_deterministic() -> None:
    registry = valid_registry()

    early = evaluate_stabilization("fixture_seasonal_metric", "pa", 8, registry, 2031)
    late = evaluate_stabilization("fixture_seasonal_metric", "pa", 8, registry, 2032)
    omitted = evaluate_stabilization("fixture_seasonal_metric", "pa", 8, registry)

    assert early.policy_id == "fixture-window-early"
    assert late.policy_id == "fixture-window-late"
    assert omitted.status == "unverified"


def test_overlapping_policies_are_ambiguous_and_unverified() -> None:
    registry = StabilizationRegistry(load_fixture("overlapping.json").policies)

    decision = evaluate_stabilization("fixture_overlap_metric", "pa", 15, registry, 2031)

    assert registry.resolve("fixture_overlap_metric", "pa", 2031) is None
    assert decision.status == "unverified"
    assert decision.reason == "no unambiguous matching policy"


def test_decision_copies_full_policy_provenance() -> None:
    decision = evaluate_stabilization("fixture_pa_metric", "pa", 12, valid_registry(), 2040)

    assert decision.policy_id == "fixture-generic-pa"
    assert decision.policy_version == "v1"
    assert decision.minimum_sample_ref == "fixture-policy:v1:pa-minimum"
    assert decision.minimum_sample_value == 12
    assert decision.source_name == "Fixture policy source"
    assert decision.source_url == "https://example.invalid/policies/fixture-pa"
    assert decision.retrieved_at_utc == "2030-02-03T04:05:06Z"


def test_apply_stabilization_returns_new_immutable_observation_and_preserves_fields() -> None:
    observation = fixture_observation()
    applied = apply_stabilization_to_observation(observation, valid_registry())

    assert applied is not observation
    assert applied.stabilization_status == "pass"
    assert asdict(applied) | {"stabilization_status": observation.stabilization_status} == asdict(observation)
    with pytest.raises(FrozenInstanceError):
        applied.notes = "changed"  # type: ignore[misc]


def test_not_applicable_decision_preserves_existing_observation_status() -> None:
    observation = fixture_observation()
    not_applicable = StabilizationDecision(
        metric_key=observation.metric_key,
        sample_type=observation.sample_type,
        sample_n=observation.sample_n,
        policy_id=None,
        policy_version=None,
        minimum_sample_ref=None,
        minimum_sample_value=None,
        source_name=None,
        source_url=None,
        retrieved_at_utc=None,
        status="not_applicable",
        reason="fixture not applicable",
    )

    with patch("hitterdna.stabilization.evaluate_stabilization", return_value=not_applicable):
        applied = apply_stabilization_to_observation(observation, valid_registry())

    assert applied is not observation
    assert applied.stabilization_status == "unverified"


def test_same_inputs_produce_deterministic_decisions() -> None:
    first = evaluate_stabilization("fixture_bip_metric", "bip", 9, valid_registry())
    second = evaluate_stabilization("fixture_bip_metric", "bip", 9, valid_registry())

    assert first == second


def test_json_schema_accepts_valid_file_and_rejects_invalid_file() -> None:
    schema = json.loads((ROOT / "schemas" / "stabilization_policy.schema.json").read_text())
    validator = jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker())
    valid = json.loads((FIXTURES / "valid.json").read_text())
    invalid = json.loads((FIXTURES / "invalid_url_timestamp.json").read_text())

    validator.validate(valid)
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(invalid)


def test_stabilization_source_contains_no_numeric_threshold_literals() -> None:
    tree = ast.parse((ROOT / "src" / "hitterdna" / "stabilization.py").read_text())
    numeric_literals = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool)
    ]

    # No exemption is needed: finite/positive validation uses float() as zero,
    # so every numeric stabilization minimum remains policy-file supplied.
    assert numeric_literals == []
