"""Read-only CLI smoke test for the MLB Stats API schedule endpoint."""

from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import date as calendar_date
import json
import sys
from typing import Any, Mapping, Sequence, TextIO

from hitterdna.statsapi import GameContext, StatsAPIClient, normalize_game_context


class SmokeTestError(ValueError):
    """Raised when a schedule response cannot safely identify its games."""


def run_schedule_smoke_test(
    date: str,
    *,
    away_team: str | None = None,
    home_team: str | None = None,
    client: StatsAPIClient,
) -> dict[str, Any]:
    """Fetch, normalize, and optionally filter one day's schedule.

    The function performs no writes. It raises ``SmokeTestError`` when the
    response cannot be normalized into games with the identifiers required for
    safe schedule use.
    """

    schedule = client.fetch_schedule(date)
    contexts = [_normalize_and_validate(game) for game in _schedule_games(schedule)]
    matching_contexts = [
        context
        for context in contexts
        if _team_matches(context.away_team, away_team)
        and _team_matches(context.home_team, home_team)
    ]

    return {
        "analysis_date": date,
        "games_returned": len(matching_contexts),
        "games": [_compact_game(context) for context in matching_contexts],
    }


def main(
    argv: Sequence[str] | None = None,
    *,
    client: StatsAPIClient | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Run the schedule smoke test and return a process exit code."""

    args = _build_parser().parse_args(argv)
    output = stdout or sys.stdout
    errors = stderr or sys.stderr

    try:
        result = run_schedule_smoke_test(
            args.date,
            away_team=args.away_team,
            home_team=args.home_team,
            client=client or StatsAPIClient(),
        )
    except (OSError, ValueError) as error:
        print(f"Schedule smoke test failed: {error}", file=errors)
        return 1

    print(json.dumps(result, separators=(",", ":"), sort_keys=True), file=output)
    if result["games_returned"] == 0:
        print("Schedule smoke test failed: no matching games returned", file=errors)
        return 1
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only MLB schedule smoke test")
    parser.add_argument("--date", required=True, type=_iso_date, help="Schedule date (YYYY-MM-DD)")
    parser.add_argument("--away-team", help="Optional away-team abbreviation filter")
    parser.add_argument("--home-team", help="Optional home-team abbreviation filter")
    return parser


def _iso_date(value: str) -> str:
    try:
        return calendar_date.fromisoformat(value).isoformat()
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be in YYYY-MM-DD format") from error


def _schedule_games(schedule: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    dates = schedule.get("dates")
    if not isinstance(dates, list):
        raise SmokeTestError("schedule response has no dates array")

    games: list[Mapping[str, Any]] = []
    for schedule_date in dates:
        if not isinstance(schedule_date, Mapping):
            raise SmokeTestError("schedule response contains a malformed date entry")
        date_games = schedule_date.get("games")
        if not isinstance(date_games, list):
            raise SmokeTestError("schedule response contains a malformed games array")
        for game in date_games:
            if not isinstance(game, Mapping):
                raise SmokeTestError("schedule response contains a malformed game entry")
            games.append(game)
    return games


def _normalize_and_validate(payload: Mapping[str, Any]) -> GameContext:
    context = normalize_game_context(payload)
    required = {
        "analysis_date": context.analysis_date,
        "game_pk": context.game_pk,
        "away_team": context.away_team,
        "home_team": context.home_team,
    }
    malformed = [field for field, value in required.items() if value is None]
    if malformed:
        raise SmokeTestError(
            f"game response has malformed required identity fields: {', '.join(malformed)}"
        )
    return context


def _team_matches(actual: str | None, requested: str | None) -> bool:
    return requested is None or actual is not None and actual.casefold() == requested.casefold()


def _compact_game(context: GameContext) -> dict[str, Any]:
    game = asdict(context)
    return {
        "game_pk": game["game_pk"],
        "away_team": game["away_team"],
        "home_team": game["home_team"],
        "venue_name": game["venue_name"],
        "game_status": game["game_status"],
        "pregame_eligibility": game["pregame_eligibility"],
        "probable_away_pitcher_name": game["probable_away_pitcher_name"],
        "probable_home_pitcher_name": game["probable_home_pitcher_name"],
    }


if __name__ == "__main__":
    raise SystemExit(main())
