"""Replay one immutable Savant snapshot through the production validation path."""

from __future__ import annotations

import argparse
from datetime import date as calendar_date
import hashlib
import json
from pathlib import Path
from typing import Any, Sequence

from hitterdna.collect_slate_inputs import (
    SAVANT_ENDPOINT_ID,
    CollectedResponse,
    _attach_savant_input,
    _validate_savant_response,
)
from hitterdna.savant_expected_stats import expected_stats_source_url


def replay_savant_snapshot(
    game_date: str, snapshot_path: Path, retrieved_at_utc: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate and attach supplied bytes without network or filesystem writes."""

    raw_bytes = snapshot_path.read_bytes()
    response = CollectedResponse(
        endpoint_id=SAVANT_ENDPOINT_ID,
        endpoint_or_url=expected_stats_source_url(int(game_date[:4])),
        submitted_parameters={},
        retrieved_at_utc=retrieved_at_utc,
        source_freshness_status="PASS",
        source_failure_status="PASS",
        raw_bytes=raw_bytes,
        raw_response_hash=hashlib.sha256(raw_bytes).hexdigest(),
        snapshot_path=snapshot_path,
        reason=None,
        fetch_status="PASS",
    )
    validated = _validate_savant_response(game_date, response)
    fixture: dict[str, Any] = {"provenance": {}, "unavailable_source_inputs": {}}
    _attach_savant_input(fixture, game_date, validated)
    report = {
        "endpoint_id": SAVANT_ENDPOINT_ID,
        "fetch_status": validated.fetch_status,
        "parser_status": validated.parser_status,
        "attachment_status": validated.attachment_status,
        "status": validated.status,
        "reason": validated.reason,
        "raw_response_hash": validated.raw_response_hash,
        "row_count_when_relevant": fixture.get("provenance", {}).get(SAVANT_ENDPOINT_ID, {}).get("row_count_when_relevant"),
        "canonical_player_mlbam_ids": fixture.get("provenance", {}).get(SAVANT_ENDPOINT_ID, {}).get("canonical_keys", {}).get("player_mlbam_id", []),
    }
    return report, fixture


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Replay a local Savant snapshot without network access")
    parser.add_argument("--date", required=True, type=_iso_date)
    parser.add_argument("--snapshot", required=True, type=Path)
    parser.add_argument("--retrieved-at-utc", required=True)
    args = parser.parse_args(argv)
    try:
        report, fixture = replay_savant_snapshot(args.date, args.snapshot, args.retrieved_at_utc)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] == "PASS" and SAVANT_ENDPOINT_ID in fixture["provenance"] else 1


def _iso_date(value: str) -> str:
    try:
        return calendar_date.fromisoformat(value).isoformat()
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be YYYY-MM-DD") from error


if __name__ == "__main__":
    raise SystemExit(main())
