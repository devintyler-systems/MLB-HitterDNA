"""Read-only, auditable daily MLB slate-context artifact builder."""

from __future__ import annotations

import argparse
from datetime import date as calendar_date
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any, Callable, Mapping, Sequence, TextIO, TypedDict

from hitterdna.lineups import GameLineups, ProbablePitcher, TeamLineup, fetch_game_lineups, is_confirmed_lineup
from hitterdna.statsapi import GameContext, StatsAPIClient


class SlateContextArtifact(TypedDict):
    artifact_type: str
    game_date: str
    retrieved_at_utc: str
    games: list[dict[str, Any]]


LineupFetcher = Callable[..., GameLineups]
_LINEUP_EXCLUSION_REASON = "game freshness state excludes pregame lineup refresh"
_UNKNOWN_FRESHNESS_REASON = "game freshness state is unknown; pregame lineup refresh is held"
_UNCONFIRMED_LINEUP_REASON = (
    "lineup does not contain exactly nine unique players in batting-order slots 1 through 9"
)


def build_slate_context(
    game_date: str,
    *,
    client: StatsAPIClient,
    retrieved_at_utc: str,
    lineup_fetcher: LineupFetcher = fetch_game_lineups,
) -> SlateContextArtifact:
    """Build an in-memory slate artifact from schedule and allowed game feeds."""

    contexts = client.fetch_game_contexts(game_date)
    _validate_game_keys(contexts)
    games = [
        _game_record(context, game_date, retrieved_at_utc, lineup_fetcher)
        for context in sorted(contexts, key=_game_sort_key)
    ]
    return {
        "artifact_type": "slate_context",
        "game_date": game_date,
        "retrieved_at_utc": retrieved_at_utc,
        "games": games,
    }


def write_slate_context(artifact: SlateContextArtifact, output_path: Path) -> None:
    """Write the artifact only to the explicit caller-supplied destination."""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main(
    argv: Sequence[str] | None = None,
    *,
    client: StatsAPIClient | None = None,
    retrieved_at_utc: str | None = None,
    lineup_fetcher: LineupFetcher = fetch_game_lineups,
    stderr: TextIO | None = None,
) -> int:
    """Run the read-only slate-context CLI and return an exit code."""

    args = _build_parser().parse_args(argv)
    errors = stderr or sys.stderr
    timestamp = retrieved_at_utc or _utc_now()
    try:
        artifact = build_slate_context(
            args.date,
            client=client or StatsAPIClient(),
            retrieved_at_utc=timestamp,
            lineup_fetcher=lineup_fetcher,
        )
        write_slate_context(artifact, Path(args.output))
    except (OSError, ValueError) as error:
        print(f"Slate context failed: {error}", file=errors)
        return 1
    return 0


def _game_record(
    context: GameContext,
    game_date: str,
    retrieved_at_utc: str,
    lineup_fetcher: LineupFetcher,
) -> dict[str, Any]:
    if context.pregame_eligibility in {"eligible_refresh", "urgent_refresh"}:
        lineups = lineup_fetcher(context, retrieved_at_utc=retrieved_at_utc)
        exclusion_reason = None
    else:
        lineups = None
        exclusion_reason = _exclusion_reason(context)

    return {
        "game_pk": context.game_pk,
        "game_date": game_date,
        "scheduled_start_utc": context.scheduled_start_utc,
        "game_state": context.game_status,
        "freshness_state": context.pregame_eligibility,
        "venue": {"venue_id": context.venue_id, "venue_name": context.venue_name},
        "away_team": _team_record(context, "away", lineups),
        "home_team": _team_record(context, "home", lineups),
        "exclusion_reason": exclusion_reason,
    }


def _team_record(
    context: GameContext, side: str, lineups: GameLineups | None
) -> dict[str, Any]:
    team_id = getattr(context, f"{side}_team_id")
    team_name = getattr(context, f"{side}_team_name") or getattr(context, f"{side}_team")
    schedule_pitcher = ProbablePitcher(
        getattr(context, f"probable_{side}_pitcher_mlbam_id"),
        getattr(context, f"probable_{side}_pitcher_name"),
        getattr(context, f"probable_{side}_pitcher_throws"),
        "probable"
        if getattr(context, f"probable_{side}_pitcher_mlbam_id") is not None
        else "unresolved",
    )
    if lineups is None:
        lineup_status, lineup_reason, hitters = "not_fetched", _not_fetched_reason(context), []
        feed_pitcher = None
    else:
        team_lineup = getattr(lineups, f"{side}_lineup")
        lineup_status, lineup_reason, hitters = _lineup_record(team_lineup, lineups)
        feed_pitcher = getattr(lineups, f"{side}_probable_pitcher")

    pitcher = _merged_pitcher(schedule_pitcher, feed_pitcher)
    return {
        "team_id": team_id,
        "team_name": team_name,
        "lineup_status": lineup_status,
        "lineup_reason": lineup_reason,
        "hitters": hitters,
        "probable_pitcher": {
            "mlbam_id": pitcher.player_mlbam_id,
            "full_name": pitcher.player_name,
            "throws": pitcher.throws,
            "status": pitcher.status,
        },
    }


def _lineup_record(team_lineup: TeamLineup, lineups: GameLineups) -> tuple[str, str | None, list[dict[str, Any]]]:
    if team_lineup.lineup_status == "confirmed" and is_confirmed_lineup(team_lineup.players):
        return (
            "confirmed",
            None,
            [
                {
                    "mlbam_id": player.player_mlbam_id,
                    "full_name": player.player_name,
                    "batting_order": player.batting_order,
                    "position": player.position,
                }
                for player in sorted(team_lineup.players, key=lambda player: player.batting_order or 0)
            ],
        )
    if lineups.lineup_fetch_status == "unavailable":
        return "unconfirmed", "game-feed lineup retrieval was unavailable", []
    if lineups.lineup_fetch_status == "malformed":
        return "unconfirmed", "game-feed lineup response was malformed", []
    return "unconfirmed", _UNCONFIRMED_LINEUP_REASON, []


def _merged_pitcher(
    schedule_pitcher: ProbablePitcher, feed_pitcher: ProbablePitcher | None
) -> ProbablePitcher:
    if feed_pitcher is not None and feed_pitcher.player_mlbam_id is not None:
        return feed_pitcher
    if schedule_pitcher.player_mlbam_id is not None:
        return schedule_pitcher
    if feed_pitcher is not None:
        return feed_pitcher
    return schedule_pitcher


def _exclusion_reason(context: GameContext) -> str:
    if context.pregame_eligibility == "hold_unknown":
        return _UNKNOWN_FRESHNESS_REASON
    return _LINEUP_EXCLUSION_REASON


def _not_fetched_reason(context: GameContext) -> str:
    return _UNKNOWN_FRESHNESS_REASON if context.pregame_eligibility == "hold_unknown" else _LINEUP_EXCLUSION_REASON


def _game_sort_key(context: GameContext) -> tuple[str, int]:
    return (context.scheduled_start_utc or "", context.game_pk or 0)


def _validate_game_keys(contexts: Sequence[GameContext]) -> None:
    game_keys = [context.game_pk for context in contexts]
    if any(not isinstance(game_pk, int) or isinstance(game_pk, bool) or game_pk < 1 for game_pk in game_keys):
        raise ValueError("schedule response has a game with a malformed game_pk")
    if len(set(game_keys)) != len(game_keys):
        raise ValueError("schedule response contains duplicate game_pk values")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only MLB slate-context artifact builder")
    parser.add_argument("--date", required=True, type=_iso_date, help="Schedule date (YYYY-MM-DD)")
    parser.add_argument("--output", required=True, help="Explicit JSON artifact output path")
    return parser


def _iso_date(value: str) -> str:
    try:
        return calendar_date.fromisoformat(value).isoformat()
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be in YYYY-MM-DD format") from error


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    raise SystemExit(main())
