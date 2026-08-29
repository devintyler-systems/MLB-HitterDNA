import ast
import json
from pathlib import Path
from typing import Any

import pytest

from hitterdna.savant_expected_stats import (
    SOURCE_NAME,
    expected_stats_source_url,
    expected_stats_to_observations,
    fetch_expected_stats,
    parse_expected_stats_payload,
)


FIXTURES = Path(__file__).parent / "fixtures" / "savant_expected_stats"
ROOT = Path(__file__).parents[1]
SEASON = 2030
RETRIEVED_AT = "2030-01-01T00:00:00Z"


class FakeResponse:
    def __init__(self, status_code: int, text: str) -> None:
        self.status_code = status_code
        self.text = text


class FakeGet:
    def __init__(self, response: FakeResponse | None = None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.calls: list[tuple[str, float]] = []

    def __call__(self, url: str, *, timeout: float) -> FakeResponse:
        self.calls.append((url, timeout))
        if self.error:
            raise self.error
        assert self.response is not None
        return self.response


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_valid_csv_parses_multiple_hitters_and_unicode_name() -> None:
    result = parse_expected_stats_payload(SEASON, fixture("valid.csv"), expected_stats_source_url(SEASON), RETRIEVED_AT)

    assert result.fetch_status == "fetched"
    assert len(result.rows) == 2
    assert result.rows[0].player_name == "Synthetic Álvarez"
    assert result.rows[0].xba == 0.27
    assert result.rows[1].xwoba == 0.32


def test_blank_numeric_fields_normalize_to_none() -> None:
    result = parse_expected_stats_payload(SEASON, fixture("blank_metrics.csv"), expected_stats_source_url(SEASON), RETRIEVED_AT)
    row = result.rows[0]

    assert result.fetch_status == "fetched"
    assert row.pa is None
    assert row.batted_ball_events is None
    assert row.xba is None
    assert row.xwoba is None


def test_missing_player_id_stays_none_without_name_resolution() -> None:
    result = parse_expected_stats_payload(SEASON, fixture("missing_id.csv"), expected_stats_source_url(SEASON), RETRIEVED_AT)

    assert result.fetch_status == "fetched"
    assert result.rows[0].player_mlbam_id is None


@pytest.mark.parametrize("name", ["missing_name.csv", "invalid_season.csv"])
def test_missing_or_invalid_structural_identity_is_rejected(name: str) -> None:
    result = parse_expected_stats_payload(SEASON, fixture(name), expected_stats_source_url(SEASON), RETRIEVED_AT)

    assert result.fetch_status == "malformed"
    assert result.rows == ()


def test_invalid_numeric_and_duplicate_id_payloads_are_malformed() -> None:
    invalid_numeric = parse_expected_stats_payload(SEASON, fixture("invalid_numeric.csv"), expected_stats_source_url(SEASON), RETRIEVED_AT)
    duplicate_ids = parse_expected_stats_payload(SEASON, fixture("duplicate_id.csv"), expected_stats_source_url(SEASON), RETRIEVED_AT)

    assert invalid_numeric.fetch_status == "malformed"
    assert duplicate_ids.fetch_status == "malformed"


def test_network_failure_and_non_200_are_unavailable() -> None:
    network = fetch_expected_stats(SEASON, http_get=FakeGet(error=OSError("fixture network")))
    non_200 = fetch_expected_stats(SEASON, http_get=FakeGet(FakeResponse(503, "fixture failure")))

    assert network.fetch_status == "unavailable"
    assert non_200.fetch_status == "unavailable"


def test_structurally_invalid_payload_is_malformed_not_empty_success() -> None:
    result = fetch_expected_stats(SEASON, http_get=FakeGet(FakeResponse(200, '{"unexpected": true}')))

    assert result.fetch_status == "malformed"
    assert result.rows == ()


def test_fetch_preserves_exact_source_url_and_uses_injected_http() -> None:
    fake_get = FakeGet(FakeResponse(200, fixture("valid.csv")))
    result = fetch_expected_stats(SEASON, http_get=fake_get)

    assert result.fetch_status == "fetched"
    assert result.source_url == expected_stats_source_url(SEASON)
    assert f"year={SEASON}" in result.source_url
    assert fake_get.calls[0][0] == result.source_url


def test_observations_map_every_required_metric_with_audit_metadata() -> None:
    row = parse_expected_stats_payload(SEASON, fixture("valid.csv"), expected_stats_source_url(SEASON), RETRIEVED_AT).rows[0]
    observations = expected_stats_to_observations(row)

    assert set(observations) == {
        "expected_batting_average",
        "expected_slugging",
        "expected_woba",
        "plate_appearances",
        "batted_ball_events",
    }
    assert observations["expected_batting_average"].value == row.xba
    assert observations["expected_slugging"].value == row.xslg
    assert observations["expected_woba"].value == row.xwoba
    assert observations["plate_appearances"].value == row.pa
    assert observations["batted_ball_events"].value == row.batted_ball_events
    assert observations["expected_batting_average"].sample_type == "pa"
    assert observations["expected_batting_average"].sample_n == row.pa
    assert observations["batted_ball_events"].sample_type == "bip"
    assert observations["batted_ball_events"].sample_n == row.batted_ball_events
    for observation in observations.values():
        assert observation.source_url == row.source_url
        assert observation.retrieved_at_utc == RETRIEVED_AT
        assert observation.source_name == SOURCE_NAME
        assert observation.stabilization_status == "unverified"
        assert observation.notes is not None
        assert "stabilization evaluation deferred" in observation.notes


def test_observations_are_deterministic_and_allow_explicit_timestamp() -> None:
    row = parse_expected_stats_payload(SEASON, fixture("valid.csv"), expected_stats_source_url(SEASON), RETRIEVED_AT).rows[0]

    assert expected_stats_to_observations(row) == expected_stats_to_observations(row)
    assert expected_stats_to_observations(row, "2030-01-02T00:00:00Z")["expected_woba"].retrieved_at_utc == "2030-01-02T00:00:00Z"


def test_adapter_has_no_thresholds_or_filter_evaluation_calls() -> None:
    source = (ROOT / "src" / "hitterdna" / "savant_expected_stats.py").read_text()
    tree = ast.parse(source)
    imports = [node for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    filter_imports = [node for node in imports if node.module == "hitterdna.filter_table"]

    assert len(filter_imports) == 1
    assert [alias.name for alias in filter_imports[0].names] == ["MetricObservation"]
    assert "evaluate_filter" not in source
    assert "ThresholdRegistry" not in source
    assert "market" not in source.casefold()
    assert "probability" not in source.casefold()


def test_adapter_source_has_no_numeric_metric_threshold_comparisons() -> None:
    source = (ROOT / "src" / "hitterdna" / "savant_expected_stats.py").read_text()
    tree = ast.parse(source)
    metric_names = {"ba", "xba", "slg", "xslg", "woba", "xwoba", "pa", "bbe"}

    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        names = {child.id for child in ast.walk(node.left) if isinstance(child, ast.Name)}
        assert not names.intersection(metric_names)


def test_synthetic_example_data_is_jsonl() -> None:
    examples = (ROOT / "data" / "savant_expected_stats.example.jsonl").read_text().splitlines()

    assert len(examples) == 4
    assert [json.loads(example)["record_type"] for example in examples] == [
        "savant_expected_stats_row",
        "savant_expected_stats_row",
        "savant_expected_stats_row",
        "metric_observations",
    ]
