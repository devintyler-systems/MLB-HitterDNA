from pathlib import Path

from hitterdna.replay_savant_snapshot import replay_savant_snapshot


FIXTURE = Path(__file__).parent / "fixtures" / "savant_expected_stats" / "captured_expected_statistics_2026.csv"
CAPTURED_EXPECTED_STATISTICS_SHA256 = "b99c978b8938767322ce03d101771e9c182f8e96a0f1fad6a9ffe5355adceb92"


def test_local_replay_uses_exact_captured_bytes_without_network() -> None:
    report, fixture = replay_savant_snapshot(
        "2026-09-07", FIXTURE, "2026-09-07T12:00:00Z",
    )

    assert report == {
        "endpoint_id": "savant_expected_statistics",
        "fetch_status": "PASS",
        "parser_status": "PASS",
        "attachment_status": "PASS",
        "status": "PASS",
        "reason": None,
        "raw_response_hash": CAPTURED_EXPECTED_STATISTICS_SHA256,
        "row_count_when_relevant": 2,
        "canonical_player_mlbam_ids": [608324, 691718],
    }
    assert fixture["provenance"]["savant_expected_statistics"]["raw_response_hash"] == CAPTURED_EXPECTED_STATISTICS_SHA256
