"""Prepare a local, schedule-only Slate Run fixture without network retrieval."""

from __future__ import annotations

import argparse
from datetime import date as calendar_date
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from hitterdna.filter_policy import load_filter_policy_contract
from hitterdna.statsapi import normalize_schedule_contexts


class SlateFixturePreparationError(ValueError):
    """The supplied file cannot be retained as a schedule-only fixture."""


_UNAVAILABLE_SOURCE_INPUTS = {
    "mlb_statsapi_lineup_confirmation": (
        "No local MLB StatsAPI live-game-feed lineup confirmation was supplied."
    ),
    "mlb_statsapi_probable_pitchers": (
        "No separate local probable or actual starter evidence was supplied; "
        "the preserved schedule snapshot is not promoted as starter evidence."
    ),
    "savant_expected_statistics": (
        "No local Baseball Savant Expected Statistics response was supplied."
    ),
}


def prepare_slate_fixture(
    game_date: str,
    schedule_path: Path,
    *,
    retrieved_at_utc: str | None = None,
) -> dict[str, Any]:
    """Create an auditable fixture from exactly one operator-supplied raw file."""

    try:
        raw_bytes = schedule_path.read_bytes()
    except OSError as error:
        raise SlateFixturePreparationError(f"schedule file is unreadable: {error}") from error
    try:
        schedule = json.loads(raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SlateFixturePreparationError(f"schedule file is not valid JSON: {error}") from error
    if not isinstance(schedule, Mapping):
        raise SlateFixturePreparationError("schedule file must contain a StatsAPI schedule object")

    games = _validate_statsapi_schedule(schedule, game_date)
    timestamp = retrieved_at_utc or _utc_now()
    if not _is_utc_timestamp(timestamp):
        raise SlateFixturePreparationError("retrieved_at_utc must be an ISO-8601 UTC timestamp")
    game_pks = [game["gamePk"] for game in games]
    policy = load_filter_policy_contract(loaded_at_utc=timestamp)
    fixture = {
        "game_date": game_date,
        "retrieved_at_utc": timestamp,
        "schedule": schedule,
        "provenance": {
            "mlb_statsapi_schedule_by_date": {
                "source": "MLB Stats API",
                "endpoint_or_url": _endpoint_descriptor(schedule, schedule_path),
                "submitted_parameters": {"sportId": 1, "date": game_date},
                "retrieved_at_utc": timestamp,
                "raw_response_hash": hashlib.sha256(raw_bytes).hexdigest(),
                "row_count_when_relevant": len(games),
                "canonical_keys": {"game_pk": game_pks},
                "source_freshness_status": "PASS",
                "source_failure_status": "PASS",
            }
        },
        "unavailable_source_inputs": {
            endpoint_id: {"status": "UNVERIFIED", "reason": reason}
            for endpoint_id, reason in _UNAVAILABLE_SOURCE_INPUTS.items()
        },
    }
    if policy.contract is not None:
        fixture["filter"] = policy.contract
    else:
        fixture["filter"] = {"policy_source_reference": "docs/filter-thresholds.md", "policy_load_error": policy.reason}
    return fixture


def write_slate_fixture(fixture: Mapping[str, Any], output_path: Path) -> None:
    """Write the prepared fixture only to the explicit operator-selected path."""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(_stable_json(fixture), encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prepare a local schedule-only HitterDNA Slate Run fixture")
    parser.add_argument("--date", required=True, type=_iso_date, help="Schedule date (YYYY-MM-DD)")
    parser.add_argument("--schedule", required=True, type=Path, help="Local raw MLB StatsAPI schedule JSON")
    parser.add_argument("--output", required=True, type=Path, help="Output local HitterDNA fixture JSON")
    args = parser.parse_args(argv)
    try:
        write_slate_fixture(prepare_slate_fixture(args.date, args.schedule), args.output)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    return 0


def _validate_statsapi_schedule(schedule: Mapping[str, Any], game_date: str) -> list[Mapping[str, Any]]:
    """Check the minimum raw Schedule API shape while retaining it unchanged."""

    for field in ("totalItems", "totalGames"):
        value = schedule.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise SlateFixturePreparationError(f"schedule file is not StatsAPI schedule-shaped: {field} is invalid")
    dates = schedule.get("dates")
    if not isinstance(dates, list):
        raise SlateFixturePreparationError("schedule file is not StatsAPI schedule-shaped: dates must be an array")
    matching_dates = [entry for entry in dates if isinstance(entry, Mapping) and entry.get("date") == game_date]
    if not matching_dates:
        raise SlateFixturePreparationError(f"schedule file does not include requested date {game_date}")
    games: list[Mapping[str, Any]] = []
    for entry in matching_dates:
        date_games = entry.get("games")
        if not isinstance(date_games, list) or not all(isinstance(game, Mapping) for game in date_games):
            raise SlateFixturePreparationError("schedule file is not StatsAPI schedule-shaped: date games are invalid")
        games.extend(date_games)
    if not games:
        raise SlateFixturePreparationError(f"schedule file has no games for requested date {game_date}")
    game_pks = [game.get("gamePk") for game in games]
    if any(isinstance(game_pk, bool) or not isinstance(game_pk, int) or game_pk < 1 for game_pk in game_pks):
        raise SlateFixturePreparationError("schedule file has a malformed gamePk")
    if len(set(game_pks)) != len(game_pks):
        raise SlateFixturePreparationError("schedule file contains duplicate gamePk values")
    # Exercise the runner's pure normalizer here; it performs no retrieval and
    # ensures the preserved target-date game objects remain usable downstream.
    contexts = normalize_schedule_contexts({"dates": matching_dates})
    if len(contexts) != len(games) or any(context.game_pk is None for context in contexts):
        raise SlateFixturePreparationError("schedule file contains unusable game context")
    return games


def _endpoint_descriptor(schedule: Mapping[str, Any], schedule_path: Path) -> str:
    for key in ("endpoint_or_url", "endpoint", "url"):
        value = schedule.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return f"local operator-supplied MLB StatsAPI schedule snapshot ({schedule_path.name})"


def _iso_date(value: str) -> str:
    try:
        return calendar_date.fromisoformat(value).isoformat()
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be YYYY-MM-DD") from error


def _is_utc_timestamp(value: str) -> bool:
    if not value.endswith(("Z", "+00:00")):
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def _stable_json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    raise SystemExit(main())
