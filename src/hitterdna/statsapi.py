"""Typed access and normalization for the MLB Stats API schedule endpoint."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

import requests


SCHEDULE_URL = "https://statsapi.mlb.com/api/v1/schedule"


class Response(Protocol):
    """The small portion of a requests response used by this client."""

    def raise_for_status(self) -> None: ...

    def json(self) -> Any: ...


class Session(Protocol):
    """The injectable HTTP dependency used by :class:`StatsAPIClient`."""

    def get(
        self, url: str, *, params: Mapping[str, str | int], timeout: float
    ) -> Response: ...


@dataclass(frozen=True)
class GameContext:
    analysis_date: str | None
    game_pk: int | None
    venue_id: int | None
    venue_name: str | None
    away_team: str | None
    home_team: str | None
    probable_away_pitcher_mlbam_id: int | None
    probable_away_pitcher_name: str | None
    probable_home_pitcher_mlbam_id: int | None
    probable_home_pitcher_name: str | None
    game_status: str | None


@dataclass
class StatsAPIClient:
    """Client for MLB's public schedule endpoint.

    HTTP retrieval is deliberately kept here; ``normalize_game_context`` is a
    pure function so saved payloads can be tested without network access.
    """

    session: Session = field(default_factory=requests.Session)
    timeout: float = 10.0

    def fetch_schedule(self, date: str) -> dict[str, Any]:
        """Fetch the MLB regular schedule payload for an ISO date."""

        response = self.session.get(
            SCHEDULE_URL,
            params={
                "sportId": 1,
                "date": date,
                "hydrate": "probablePitcher,venue",
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("MLB Stats API schedule response must be an object")
        return payload

    def resolve_game_context(
        self, date: str, away_team: str, home_team: str
    ) -> GameContext | None:
        """Return the unique schedule game matching the two supplied teams.

        A missing or non-unique match is intentionally unresolved rather than
        guessed. Team comparison accepts common official name and abbreviation
        representations returned by the Stats API.
        """

        schedule = self.fetch_schedule(date)
        matches = [
            normalize_game_context(game)
            for game in _schedule_games(schedule)
            if _team_matches(game, "away", away_team)
            and _team_matches(game, "home", home_team)
        ]
        return matches[0] if len(matches) == 1 else None


def normalize_game_context(payload: Mapping[str, Any]) -> GameContext:
    """Normalize one raw Stats API ``game`` object without making HTTP calls."""

    teams = _as_mapping(payload.get("teams"))
    away = _as_mapping(teams.get("away"))
    home = _as_mapping(teams.get("home"))
    venue = _as_mapping(payload.get("venue"))
    status = _as_mapping(payload.get("status"))
    away_pitcher = _as_mapping(away.get("probablePitcher"))
    home_pitcher = _as_mapping(home.get("probablePitcher"))

    return GameContext(
        analysis_date=_analysis_date(payload),
        game_pk=_positive_integer(payload.get("gamePk")),
        venue_id=_positive_integer(venue.get("id")),
        venue_name=_string(venue.get("name")),
        away_team=_team_name(away),
        home_team=_team_name(home),
        probable_away_pitcher_mlbam_id=_positive_integer(away_pitcher.get("id")),
        probable_away_pitcher_name=_string(away_pitcher.get("fullName")),
        probable_home_pitcher_mlbam_id=_positive_integer(home_pitcher.get("id")),
        probable_home_pitcher_name=_string(home_pitcher.get("fullName")),
        game_status=_string(status.get("detailedState") or status.get("abstractGameState")),
    )


def _schedule_games(schedule: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    games: list[Mapping[str, Any]] = []
    dates = schedule.get("dates")
    if not isinstance(dates, list):
        return games
    for schedule_date in dates:
        if not isinstance(schedule_date, Mapping):
            continue
        date_games = schedule_date.get("games")
        if not isinstance(date_games, list):
            continue
        games.extend(game for game in date_games if isinstance(game, Mapping))
    return games


def _team_matches(game: Mapping[str, Any], side: str, requested_team: str) -> bool:
    teams = _as_mapping(game.get("teams"))
    matchup_side = _as_mapping(teams.get(side))
    team = _as_mapping(matchup_side.get("team"))
    requested = _team_key(requested_team)
    candidates = (team.get("name"), team.get("abbreviation"), team.get("teamCode"))
    return bool(requested) and any(_team_key(value) == requested for value in candidates)


def _team_name(side: Mapping[str, Any]) -> str | None:
    team = _as_mapping(side.get("team"))
    return _string(team.get("abbreviation") or team.get("name"))


def _analysis_date(payload: Mapping[str, Any]) -> str | None:
    official_date = _string(payload.get("officialDate"))
    if official_date:
        return official_date
    game_date = _string(payload.get("gameDate"))
    return game_date[:10] if game_date else None


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _positive_integer(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 1 else None


def _string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _team_key(value: Any) -> str:
    return "".join(str(value).casefold().split()) if isinstance(value, str) else ""
