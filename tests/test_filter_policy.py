import hashlib
import json
from pathlib import Path

import pytest

from hitterdna.filter_policy import load_filter_policy_contract


ROOT = Path(__file__).parents[1]
LOADED_AT = "2030-01-01T00:00:00Z"


def _policy_document(policy: object) -> str:
    return (
        "# local policy\n"
        "<!-- HITTERDNA_FILTER_POLICY_BEGIN -->\n"
        "```json\n"
        + json.dumps({"filter_policy": policy})
        + "\n```\n"
        "<!-- HITTERDNA_FILTER_POLICY_END -->\n"
    )


def _definition(**overrides: object) -> dict[str, object]:
    value = {
        "filter_id": "required-xba", "filter_version": "test-v1", "filter_name": "xBA present",
        "required": True, "metric_key": "expected_batting_average", "operator": "present",
        "threshold_ref": None, "allowed_values": None, "custom_rule_id": None,
        "description": "fixture",
    }
    value.update(overrides)
    return value


def test_authoritative_policy_load_is_deterministic_and_records_content_identity() -> None:
    result = load_filter_policy_contract(loaded_at_utc=LOADED_AT)
    assert result.load_status == "loaded"
    assert result.contract is not None
    assert result.contract["policy_source_reference"] == "docs/filter-thresholds.md"
    assert result.contract["policy_content_sha256"] == hashlib.sha256(
        (ROOT / "docs" / "filter-thresholds.md").read_bytes()
    ).hexdigest()
    assert result.contract["policy_loaded_at_utc"] == LOADED_AT
    assert result.contract["policy_version"] == "filter-thresholds-v0.1"
    assert [item["filter_id"] for item in result.contract["definitions"]] == [
        "H1-xba-present", "H1-pa-present",
    ]


@pytest.mark.parametrize(
    ("content", "expected_reason"),
    [
        ("# no contract\n", "FILTER_POLICY_MALFORMED"),
        (_policy_document({"policy_version": "test-v1", "definitions": [], "thresholds": {}}), "FILTER_POLICY_NO_REQUIRED_DEFINITIONS"),
        (_policy_document({"policy_version": "test-v1", "definitions": [_definition(operator="gte", threshold_ref="missing")], "thresholds": {}}), "FILTER_POLICY_REQUIRED_THRESHOLD_MISSING"),
    ],
)
def test_malformed_or_incomplete_policy_fails_closed(tmp_path: Path, content: str, expected_reason: str) -> None:
    path = tmp_path / "filter-policy.md"
    path.write_text(content, encoding="utf-8")
    result = load_filter_policy_contract(
        {"policy_source_reference": str(path)}, loaded_at_utc=LOADED_AT,
    )
    assert result.load_status == "unverified"
    assert result.contract is None
    assert result.reason is not None and result.reason.startswith(expected_reason)


def test_recorded_hash_mismatch_fails_closed() -> None:
    result = load_filter_policy_contract(
        {"policy_content_sha256": "0" * 64}, loaded_at_utc=LOADED_AT,
    )
    assert result.load_status == "unverified"
    assert result.reason == "FILTER_POLICY_HASH_MISMATCH: recorded policy hash does not match source bytes"
