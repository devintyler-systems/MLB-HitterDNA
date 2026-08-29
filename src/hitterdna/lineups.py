"""Read-only confirmed-lineup ingestion from the MLB Stats API game feed."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Callable, Literal, Mapping, Sequence

import requests

from hitterdna.statsapi import GameContext, PregameEligibility


GAME_FEED_URL = "https://statsapi.mlb.com/api/v1.1/game/{game_pk}/feed/live"
LineupStatus = Literal["confirmed", "unconfirmed"]
LineupFetchStatus = Literal["fetched", "unavailable", "malformed", "ineligible_game"]


@dataclass(frozen=True)
class LineupPlayer:
    game_pk: int
    team_id: int | None
    team_abbreviation: str
    player_mlbam_id: int | None
    player_name: str
    lineup_status: LineupStatus
    batting_order: int | None
    position: str | None
    source_url: str
    retrieved_at_utc: str


@dataclass(frozen=True)
class TeamLineup:
    team_id: int | None
    team_abbreviation: str
    lineup_status: LineupStatus
    players: tuple[LineupPlayer, ...]


@dataclass(frozen=True)
class GameLineups:
    game_pk: int | None
    away_lineup: TeamLineup
    home_lineup: TeamLineup
    source_url: str
    retrieved_at_utc: str
    pregame_eligibility: PregameEligibility
    lineup_fetch_status: LineupFetchStatus


@dataclass(frozen=True)
class LineupFetchResult:
    """Explicit fetch result alias retained for callers that prefer this name."""

    game_lineups: GameLineups


HttpGet = Callable[..., Any]


def fetch_game_lineups(
    game_context: GameContext, http_get: HttpGet = requests.get
) -> GameLineups:
    """Fetch lineups only for pregame-refresh-eligible schedule events.

    The function makes no persistent writes. It never calls the feed for an
    ineligible game, and delegates all payload decisions to the pure
    ``normalize_game_lineups`` function.
    """

    retrieved_at_utc = _utc_now()
    source_url = game_feed_url(game_context.game_pk)
    if game_context.pregame_eligibility not in {"eligible_refresh", "urgent_refresh"}:
        return _empty_game_lineups(
            game_context,
            source_url=source_url,
            retrieved_at_utc=retrieved_at_utc,
            lineup_fetch_status="ineligible_game",
        )
    if not _is_positive_integer(game_context.game_pk):
        return _empty_game_lineups(
            game_context,
            source_url=source_url,
            retrieved_at_utc=retrieved_at_utc,
            lineup_fetch_status="malformed",
        )

    try:
        response = http_get(source_url, timeout=10.0)
        response.raise_for_status()
        payload = response.json()
    except (OSError, requests.RequestException, ValueError):
        return _empty_game_lineups(
            game_context,
            source_url=source_url,
            retrieved_at_utc=retrieved_at_utc,
            lineup_fetch_status="unavailable",
        )

    if not isinstance(payload, dict):
        return _empty_game_lineups(
            game_context,
            source_url=source_url,
            retrieved_at_utc=retrieved_at_utc,
            lineup_fetch_status="malformed",
        )
    return normalize_game_lineups(game_context, payload, retrieved_at_utc)


def normalize_game_lineups(
    game_context: GameContext, payload: dict[str, Any], retrieved_at_utc: str
) -> GameLineups:
    """Purely normalize an MLB live-feed payload into conservative lineups.

    A feed-side ``battingOrder`` is the only lineup evidence accepted here.
    Player, roster, active-status, and position fields are used only to
    describe entries already present in that ordered lineup evidence.
    """

    source_url = game_feed_url(game_context.game_pk)
    if not _is_positive_integer(game_context.game_pk):
        return _empty_game_lineups(
            game_context,
            source_url=source_url,
            retrieved_at_utc=retrieved_at_utc,
            lineup_fetch_status="malformed",
        )

    live_data = _mapping(payload.get("liveData"))
    boxscore = _mapping(live_data.get("boxscore"))
    teams = _mapping(boxscore.get("teams"))
    if not teams:
        return _empty_game_lineups(
            game_context,
            source_url=source_url,
            retrieved_at_utc=retrieved_at_utc,
            lineup_fetch_status="malformed",
        )

    away_lineup = _normalize_team_lineup(
        _mapping(teams.get("away")),
        game_pk=game_context.game_pk,
        fallback_abbreviation=game_context.away_team or "",
        source_url=source_url,
        retrieved_at_utc=retrieved_at_utc,
    )
    home_lineup = _normalize_team_lineup(
        _mapping(teams.get("home")),
        game_pk=game_context.game_pk,
        fallback_abbreviation=game_context.home_team or "",
        source_url=source_url,
        retrieved_at_utc=retrieved_at_utc,
    )
    return GameLineups(
        game_pk=game_context.game_pk,
        away_lineup=away_lineup,
        home_lineup=home_lineup,
        source_url=source_url,
        retrieved_at_utc=retrieved_at_utc,
        pregame_eligibility=game_context.pregame_eligibility,
        lineup_fetch_status="fetched",
    )


def is_confirmed_lineup(players: Sequence[LineupPlayer]) -> bool:
    """Return whether players establish an exact, valid nine-player lineup."""

    if len(players) != 9 or any(player.lineup_status != "confirmed" for player in players):
        return False
    player_ids = [player.player_mlbam_id for player in players]
    batting_orders = [player.batting_order for player in players]
    return (
        all(_is_positive_integer(player_id) for player_id in player_ids)
        and len(set(player_ids)) == 9
        and all(isinstance(order, int) for order in batting_orders)
        and set(batting_orders) == set(range(1, 10))
    )


def normalize_batting_order(value: Any) -> int | None:
    """Normalize official 1--9 or 100--900 batting-order representations."""

    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped.isdigit():
            return None
        value = int(stripped)
    if not isinstance(value, int):
        return None
    if 1 <= value <= 9:
        return value
    if 100 <= value <= 900 and value % 100 == 0:
        return value // 100
    return None


def candidate_eligible_from_lineup(player: LineupPlayer) -> bool:
    """Allow only a fully identified player in a confirmed batting slot."""

    return (
        player.lineup_status == "confirmed"
        and _is_positive_integer(player.game_pk)
        and _is_positive_integer(player.player_mlbam_id)
        and isinstance(player.batting_order, int)
        and 1 <= player.batting_order <= 9
    )


def game_feed_url(game_pk: int | None) -> str:
    """Return the authoritative feed URL for a valid game key, else empty."""

    return GAME_FEED_URL.format(game_pk=game_pk) if _is_positive_integer(game_pk) else ""


def _normalize_team_lineup(
    payload: Mapping[str, Any],
    *,
    game_pk: int,
    fallback_abbreviation: str,
    source_url: str,
    retrieved_at_utc: str,
) -> TeamLineup:
    team = _mapping(payload.get("team"))
    team_id = _positive_integer(team.get("id"))
    abbreviation = _string(team.get("abbreviation")) or fallback_abbreviation
    player_records = _mapping(payload.get("players"))
    batting_order = payload.get("battingOrder")
    if not isinstance(batting_order, list):
        return TeamLineup(team_id, abbreviation, "unconfirmed", ())

    provisional_players = tuple(
        _lineup_player_from_order_entry(
            order_entry,
            player_records=player_records,
            game_pk=game_pk,
            team_id=team_id,
            team_abbreviation=abbreviation,
            source_url=source_url,
            retrieved_at_utc=retrieved_at_utc,
        )
        for order_entry in batting_order
    )
    confirmed_players = tuple(
        replace(player, lineup_status="confirmed") for player in provisional_players
    )
    if is_confirmed_lineup(confirmed_players):
        return TeamLineup(team_id, abbreviation, "confirmed", confirmed_players)

    unconfirmed_players = tuple(
        replace(player, lineup_status="unconfirmed", batting_order=None)
        for player in provisional_players
    )
    return TeamLineup(team_id, abbreviation, "unconfirmed", unconfirmed_players)


def _lineup_player_from_order_entry(
    order_entry: Any,
    *,
    player_records: Mapping[str, Any],
    game_pk: int,
    team_id: int | None,
    team_abbreviation: str,
    source_url: str,
    retrieved_at_utc: str,
) -> LineupPlayer:
    player_record = _player_record(player_records, order_entry)
    person = _mapping(player_record.get("person"))
    batting = _mapping(_mapping(player_record.get("stats")).get("batting"))
    position = _mapping(player_record.get("position"))
    return LineupPlayer(
        game_pk=game_pk,
        team_id=team_id,
        team_abbreviation=team_abbreviation,
        player_mlbam_id=_positive_integer(person.get("id")),
        player_name=_string(person.get("fullName")) or "",
        lineup_status="unconfirmed",
        batting_order=normalize_batting_order(batting.get("battingOrder")),
        position=_string(position.get("abbreviation")),
        source_url=source_url,
        retrieved_at_utc=retrieved_at_utc,
    )


def _player_record(player_records: Mapping[str, Any], order_entry: Any) -> Mapping[str, Any]:
    if isinstance(order_entry, bool) or not isinstance(order_entry, (int, str)):
        return {}
    order_id = str(order_entry)
    return _mapping(player_records.get(f"ID{order_id}") or player_records.get(order_id))


def _empty_game_lineups(
    game_context: GameContext,
    *,
    source_url: str,
    retrieved_at_utc: str,
    lineup_fetch_status: LineupFetchStatus,
) -> GameLineups:
    return GameLineups(
        game_pk=game_context.game_pk,
        away_lineup=TeamLineup(None, game_context.away_team or "", "unconfirmed", ()),
        home_lineup=TeamLineup(None, game_context.home_team or "", "unconfirmed", ()),
        source_url=source_url,
        retrieved_at_utc=retrieved_at_utc,
        pregame_eligibility=game_context.pregame_eligibility,
        lineup_fetch_status=lineup_fetch_status,
    )


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _is_positive_integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def _positive_integer(value: Any) -> int | None:
    return value if _is_positive_integer(value) else None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
