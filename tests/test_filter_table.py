import ast
from dataclasses import replace
import json
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from hitterdna.filter_table import (
    FilterDefinition,
    MetricObservation,
    ThresholdRegistry,
    build_candidate_filter_table,
    candidate_can_advance,
    evaluate_filter,
    serialize_filter_table,
)
from hitterdna.lineups import LineupPlayer
from hitterdna.statsapi import GameContext


FIXTURES = Path(__file__).parent / "fixtures" / "filter_table"
ROOT = Path(__file__).parents[1]


def load_baseline() -> dict[str, Any]:
    return json.loads((FIXTURES / "baseline.json").read_text())


def baseline_models() -> tuple[str, GameContext, LineupPlayer, MetricObservation, ThresholdRegistry, FilterDefinition]:
    payload = load_baseline()
    return (
        payload["analysis_date"],
        GameContext(**payload["game_context"]),
        LineupPlayer(**payload["lineup_player"]),
        MetricObservation(**payload["observation"]),
        ThresholdRegistry(**payload["threshold_registry"]),
        FilterDefinition(**payload["definition"]),
    )


def build_table(
    *,
    game_context: GameContext | None = None,
    player: LineupPlayer | None = None,
    definitions: tuple[FilterDefinition, ...] | None = None,
    observations: dict[str, MetricObservation] | None = None,
    thresholds: ThresholdRegistry | None = None,
    custom_rules: dict[str, Any] | None = None,
):
    analysis_date, baseline_game, baseline_player, observation, registry, definition = baseline_models()
    return build_candidate_filter_table(
        analysis_date,
        game_context or baseline_game,
        player or baseline_player,
        "OPP",
        definitions if definitions is not None else (definition,),
        observations if observations is not None else {observation.metric_key: observation},
        thresholds or registry,
        custom_rules,
    )


def test_valid_candidate_with_all_required_passes_advances() -> None:
    table = build_table()

    assert table.candidate_disposition == "advance"
    assert table.results[0].status == "PASS"
    assert candidate_can_advance(table) is True


def test_required_missing_metric_is_unverified_and_drops() -> None:
    table = build_table(observations={})

    assert table.results[0].status == "UNVERIFIED"
    assert table.candidate_disposition == "drop"


def test_required_missing_threshold_is_unverified_and_drops() -> None:
    _, _, _, _, registry, _ = baseline_models()
    missing_registry = replace(registry, values={})
    table = build_table(thresholds=missing_registry)

    assert table.results[0].status == "UNVERIFIED"
    assert table.candidate_disposition == "drop"


@pytest.mark.parametrize("field", ["source_url", "retrieved_at_utc", "sample_type", "sample_n"])
def test_required_metric_missing_audit_field_is_unverified_and_drops(field: str) -> None:
    _, _, _, observation, _, _ = baseline_models()
    missing_value: Any = None if field == "sample_n" else ""
    table = build_table(observations={observation.metric_key: replace(observation, **{field: missing_value})})

    assert table.results[0].status == "UNVERIFIED"
    assert table.candidate_disposition == "drop"


@pytest.mark.parametrize("stabilization_status", ["fail", "unverified"])
def test_required_unstable_metric_is_unverified_and_drops(stabilization_status: str) -> None:
    _, _, _, observation, _, _ = baseline_models()
    table = build_table(
        observations={observation.metric_key: replace(observation, stabilization_status=stabilization_status)}
    )

    assert table.results[0].status == "UNVERIFIED"
    assert table.candidate_disposition == "drop"


@pytest.mark.parametrize(
    ("operator", "actual_value", "threshold_value", "expected"),
    [
        ("gte", 8, 8, "PASS"), ("gte", 7, 8, "FAIL"),
        ("gt", 9, 8, "PASS"), ("gt", 8, 8, "FAIL"),
        ("lte", 8, 8, "PASS"), ("lte", 9, 8, "FAIL"),
        ("lt", 7, 8, "PASS"), ("lt", 8, 8, "FAIL"),
        ("eq", "same", "same", "PASS"), ("eq", "other", "same", "FAIL"),
    ],
)
def test_declared_comparison_operators_pass_and_fail(
    operator: str, actual_value: Any, threshold_value: Any, expected: str
) -> None:
    _, _, _, observation, registry, definition = baseline_models()
    definition = replace(definition, operator=operator)
    observation = replace(observation, value=actual_value)
    registry = replace(registry, values={definition.threshold_ref: threshold_value})
    table = build_table(
        definitions=(definition,), observations={observation.metric_key: observation}, thresholds=registry
    )

    assert table.results[0].status == expected


@pytest.mark.parametrize(("actual_value", "expected"), [("allowed", "PASS"), ("blocked", "FAIL")])
def test_in_operator_passes_and_fails(actual_value: str, expected: str) -> None:
    _, _, _, observation, registry, definition = baseline_models()
    definition = replace(definition, operator="in", threshold_ref=None, allowed_values=("allowed",))
    observation = replace(observation, value=actual_value)
    table = build_table(
        definitions=(definition,), observations={observation.metric_key: observation}, thresholds=registry
    )

    assert table.results[0].status == expected


def test_unsupported_operator_and_comparison_type_error_are_unverified() -> None:
    _, _, _, observation, registry, definition = baseline_models()
    unsupported = replace(definition, operator="unsupported")
    type_error_observation = replace(observation, value="not-numeric")

    unsupported_table = build_table(definitions=(unsupported,))
    type_error_table = build_table(
        observations={observation.metric_key: type_error_observation}, thresholds=registry
    )

    assert unsupported_table.results[0].status == "UNVERIFIED"
    assert type_error_table.results[0].status == "UNVERIFIED"


@pytest.mark.parametrize("status", ["PASS", "FAIL", "UNVERIFIED"])
def test_custom_rule_can_return_each_supported_status(status: str) -> None:
    _, _, _, observation, registry, definition = baseline_models()
    definition = replace(definition, operator="custom", threshold_ref=None, custom_rule_id="fixture-rule")
    table = build_table(
        definitions=(definition,),
        observations={observation.metric_key: observation},
        thresholds=registry,
        custom_rules={"fixture-rule": lambda *_: (status, f"fixture {status}")},
    )

    assert table.results[0].status == status


def test_custom_rule_missing_or_raising_is_unverified() -> None:
    _, _, _, observation, registry, definition = baseline_models()
    definition = replace(definition, operator="custom", threshold_ref=None, custom_rule_id="fixture-rule")

    missing = build_table(definitions=(definition,), observations={observation.metric_key: observation})
    raising = build_table(
        definitions=(definition,),
        observations={observation.metric_key: observation},
        thresholds=registry,
        custom_rules={"fixture-rule": lambda *_: (_ for _ in ()).throw(RuntimeError("fixture"))},
    )

    assert missing.results[0].status == "UNVERIFIED"
    assert raising.results[0].status == "UNVERIFIED"


def test_optional_fail_and_unverified_do_not_block_advance() -> None:
    _, _, _, observation, registry, required_definition = baseline_models()
    optional_fail = replace(required_definition, filter_id="optional-fail", required=False)
    optional_unverified = replace(required_definition, filter_id="optional-unverified", required=False, metric_key="absent")
    failing_observation = replace(observation, value=0)
    table = build_table(
        definitions=(required_definition, optional_fail, optional_unverified),
        observations={observation.metric_key: observation},
        thresholds=registry,
    )
    failing_table = build_table(
        definitions=(required_definition, optional_fail),
        observations={observation.metric_key: failing_observation},
        thresholds=registry,
    )

    assert table.candidate_disposition == "advance"
    assert table.results[1].status == "PASS"
    assert table.results[2].status == "UNVERIFIED"
    assert failing_table.candidate_disposition == "drop"


def test_optional_failure_does_not_block_when_required_filter_passes() -> None:
    _, _, _, observation, registry, required_definition = baseline_models()
    optional = replace(required_definition, filter_id="optional", required=False, metric_key="optional_metric")
    optional_observation = replace(observation, metric_key="optional_metric", value=0)
    table = build_table(
        definitions=(required_definition, optional),
        observations={observation.metric_key: observation, optional.metric_key: optional_observation},
        thresholds=registry,
    )

    assert [result.status for result in table.results] == ["PASS", "FAIL"]
    assert table.candidate_disposition == "advance"


def test_ineligible_game_and_unconfirmed_lineup_produce_explicit_intake_failures() -> None:
    _, game_context, player, _, _, _ = baseline_models()
    ineligible = build_table(game_context=replace(game_context, pregame_eligibility="hold_unknown"))
    unconfirmed = build_table(player=replace(player, lineup_status="unconfirmed"))

    for table in (ineligible, unconfirmed):
        assert table.candidate_disposition == "drop"
        assert len(table.results) == 1
        assert table.results[0].filter_id == "intake-eligibility"
        assert table.results[0].status == "FAIL"


@pytest.mark.parametrize("player_change", [
    {"player_mlbam_id": None},
    {"player_mlbam_id": 0},
    {"batting_order": None},
    {"batting_order": 10},
])
def test_invalid_player_identity_or_order_produces_explicit_intake_failure(player_change: dict[str, Any]) -> None:
    _, _, player, _, _, _ = baseline_models()
    table = build_table(player=replace(player, **player_change))

    assert table.candidate_disposition == "drop"
    assert table.results[0].filter_id == "intake-eligibility"
    assert table.results[0].status == "FAIL"


def test_serialized_result_preserves_every_audit_field_and_validates_schema() -> None:
    table = build_table()
    serialized = serialize_filter_table(table)
    result = serialized["results"][0]
    schema = json.loads((ROOT / "schemas" / "filter_table.schema.json").read_text())

    assert result["actual_value"] == 8
    assert result["sample_type"] == "synthetic_sample"
    assert result["sample_n"] == 100
    assert result["metric_source_url"] == "https://example.invalid/metrics/synthetic_metric"
    assert result["metric_retrieved_at_utc"] == "2030-01-01T00:00:00Z"
    assert result["threshold_source_url"] == "https://example.invalid/thresholds/v1"
    assert result["threshold_retrieved_at_utc"] == "2030-01-01T00:00:00Z"
    jsonschema.validate(serialized, schema)


def test_schema_rejects_missing_required_audit_field_and_extra_properties() -> None:
    schema = json.loads((ROOT / "schemas" / "filter_table.schema.json").read_text())
    serialized = serialize_filter_table(build_table())
    missing_field = json.loads(json.dumps(serialized))
    del missing_field["results"][0]["metric_source_url"]
    extra_property = json.loads(json.dumps(serialized))
    extra_property["results"][0]["unexpected"] = "not allowed"

    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(missing_field, schema)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(extra_property, schema)


def test_schema_accepts_explicit_failed_intake_with_null_identity() -> None:
    _, _, player, _, _, _ = baseline_models()
    schema = json.loads((ROOT / "schemas" / "filter_table.schema.json").read_text())
    serialized = serialize_filter_table(build_table(player=replace(player, player_mlbam_id=None)))

    jsonschema.validate(serialized, schema)


def test_synthetic_filter_table_examples_validate_schema() -> None:
    schema = json.loads((ROOT / "schemas" / "filter_table.schema.json").read_text())
    examples = (ROOT / "data" / "filter_table.example.jsonl").read_text().splitlines()

    for example in examples:
        jsonschema.validate(json.loads(example), schema)


def test_serialization_is_deterministic_and_definition_order_is_preserved() -> None:
    _, _, _, observation, registry, first = baseline_models()
    second = replace(first, filter_id="second", filter_name="Second filter")
    inputs = {
        "definitions": (second, first),
        "observations": {observation.metric_key: observation},
        "thresholds": registry,
    }
    first_table = build_table(**inputs)
    second_table = build_table(**inputs)

    first_serialized = json.dumps(serialize_filter_table(first_table), separators=(",", ":"), sort_keys=True)
    second_serialized = json.dumps(serialize_filter_table(second_table), separators=(",", ":"), sort_keys=True)
    assert first_serialized == second_serialized
    assert [result.filter_id for result in first_table.results] == ["second", "synthetic-required"]


def test_threshold_registry_is_immutable_and_missing_lookup_is_none() -> None:
    _, _, _, _, registry, _ = baseline_models()

    with pytest.raises(TypeError):
        registry.values["new"] = "value"
    assert registry.resolve("absent") is None


def test_filter_table_source_has_no_hard_coded_numeric_comparison_thresholds() -> None:
    source_path = ROOT / "src" / "hitterdna" / "filter_table.py"
    tree = ast.parse(source_path.read_text())
    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent

    numeric_comparisons = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Compare)
        and any(isinstance(value, ast.Constant) and isinstance(value.value, (int, float)) for value in node.comparators)
    ]
    for comparison in numeric_comparisons:
        ancestor = comparison
        while ancestor in parents and not isinstance(ancestor, (ast.FunctionDef, ast.AsyncFunctionDef)):
            ancestor = parents[ancestor]
        assert isinstance(ancestor, ast.FunctionDef)
        # The sole exemption is positive-ID intake validation, not a filter threshold.
        assert ancestor.name == "_is_positive_integer"
