from dataclasses import replace
import json
from pathlib import Path
from typing import Any

import pytest

from hitterdna.lineups import (
    candidate_eligible_from_lineup,
    fetch_game_lineups,
    is_confirmed_lineup,
    normalize_batting_order,
    normalize_game_lineups,
)
from hitterdna.statsapi import GameContext, PregameEligibility


FIXTURES = Path(__file__).parent / "fixtures" / "lineups"
RETRIEVED_AT = "2026-08-29T19:13:00Z"


class FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self.payload


class FakeGet:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.calls: list[tuple[str, float]] = []

    def __call__(self, url: str, *, timeout: float) -> FakeResponse:
        self.calls.append((url, timeout))
        return FakeResponse(self.payload)


def load_confirmed_game() -> dict[str, Any]:
    return json.loads((FIXTURES / "confirmed_game.json").read_text())


def game_context(
    game_pk: int = 823986,
    pregame_eligibility: PregameEligibility = "eligible_refresh",
) -> GameContext:
    return GameContext(
        analysis_date="2026-08-29",
        game_pk=game_pk,
        venue_id=1,
        venue_name="Fixture Park",
        away_team="HOU",
        home_team="NYM",
        probable_away_pitcher_mlbam_id=None,
        probable_away_pitcher_name=None,
        probable_home_pitcher_mlbam_id=None,
        probable_home_pitcher_name=None,
        game_status="Scheduled",
        pregame_eligibility=pregame_eligibility,
    )


def test_valid_confirmed_away_and_home_lineups_normalize_all_nine_slots() -> None:
    lineups = normalize_game_lineups(game_context(), load_confirmed_game(), RETRIEVED_AT)

    assert lineups.lineup_fetch_status == "fetched"
    assert lineups.away_lineup.lineup_status == "confirmed"
    assert lineups.home_lineup.lineup_status == "confirmed"
    assert [player.batting_order for player in lineups.away_lineup.players] == list(range(1, 10))
    assert [player.batting_order for player in lineups.home_lineup.players] == list(range(1, 10))
    assert lineups.away_lineup.players[0].position == "DH"
    assert all(player.lineup_status == "confirmed" for player in lineups.home_lineup.players)
    assert is_confirmed_lineup(lineups.away_lineup.players) is True


def test_only_incomplete_side_is_unconfirmed_for_partial_lineup_feed() -> None:
    payload = load_confirmed_game()
    away = payload["liveData"]["boxscore"]["teams"]["away"]
    away["battingOrder"].pop()
    del away["players"]["ID109"]

    lineups = normalize_game_lineups(game_context(), payload, RETRIEVED_AT)

    assert lineups.away_lineup.lineup_status == "unconfirmed"
    assert len(lineups.away_lineup.players) == 8
    assert all(player.batting_order is None for player in lineups.away_lineup.players)
    assert lineups.home_lineup.lineup_status == "confirmed"


def test_duplicate_player_id_makes_side_unconfirmed() -> None:
    payload = load_confirmed_game()
    payload["liveData"]["boxscore"]["teams"]["away"]["battingOrder"][-1] = 101

    lineups = normalize_game_lineups(game_context(), payload, RETRIEVED_AT)

    assert lineups.away_lineup.lineup_status == "unconfirmed"


def test_duplicate_batting_slot_makes_side_unconfirmed() -> None:
    payload = load_confirmed_game()
    payload["liveData"]["boxscore"]["teams"]["away"]["players"]["ID109"]["stats"]["batting"]["battingOrder"] = "100"

    lineups = normalize_game_lineups(game_context(), payload, RETRIEVED_AT)

    assert lineups.away_lineup.lineup_status == "unconfirmed"


def test_missing_player_id_makes_side_unconfirmed() -> None:
    payload = load_confirmed_game()
    del payload["liveData"]["boxscore"]["teams"]["away"]["players"]["ID101"]["person"]["id"]

    lineups = normalize_game_lineups(game_context(), payload, RETRIEVED_AT)

    assert lineups.away_lineup.lineup_status == "unconfirmed"


@pytest.mark.parametrize("value", [None, "not-a-number", "1000"])
def test_missing_or_invalid_batting_order_makes_side_unconfirmed(value: Any) -> None:
    payload = load_confirmed_game()
    payload["liveData"]["boxscore"]["teams"]["away"]["players"]["ID101"]["stats"]["batting"]["battingOrder"] = value

    lineups = normalize_game_lineups(game_context(), payload, RETRIEVED_AT)

    assert lineups.away_lineup.lineup_status == "unconfirmed"


def test_ineligible_game_does_not_call_http() -> None:
    fake_get = FakeGet(load_confirmed_game())

    lineups = fetch_game_lineups(
        game_context(pregame_eligibility="exclude_in_progress"), http_get=fake_get
    )

    assert lineups.lineup_fetch_status == "ineligible_game"
    assert fake_get.calls == []


def test_warmup_game_fetches_and_normalizes() -> None:
    fake_get = FakeGet(load_confirmed_game())

    lineups = fetch_game_lineups(
        game_context(pregame_eligibility="urgent_refresh"), http_get=fake_get
    )

    assert lineups.lineup_fetch_status == "fetched"
    assert lineups.away_lineup.lineup_status == "confirmed"
    assert fake_get.calls[0][0].endswith("/823986/feed/live")


def test_doubleheader_events_remain_distinct_by_game_pk() -> None:
    first = normalize_game_lineups(game_context(823986), load_confirmed_game(), RETRIEVED_AT)
    second = normalize_game_lineups(game_context(823987), load_confirmed_game(), RETRIEVED_AT)

    assert first.game_pk == 823986
    assert second.game_pk == 823987
    assert first.source_url != second.source_url


def test_source_url_and_retrieval_timestamp_are_preserved() -> None:
    lineups = normalize_game_lineups(game_context(), load_confirmed_game(), RETRIEVED_AT)

    assert "/823986/feed/live" in lineups.source_url
    assert lineups.retrieved_at_utc == RETRIEVED_AT
    assert lineups.away_lineup.players[0].source_url == lineups.source_url
    assert lineups.away_lineup.players[0].retrieved_at_utc == RETRIEVED_AT


def test_batting_order_normalizer_accepts_hundred_slots_only() -> None:
    assert [normalize_batting_order(value) for value in range(100, 1000, 100)] == list(range(1, 10))
    assert normalize_batting_order("200") == 2
    assert normalize_batting_order(0) is None
    assert normalize_batting_order(1000) is None


def test_candidate_eligibility_rejects_all_unconfirmed_or_invalid_players() -> None:
    confirmed = normalize_game_lineups(game_context(), load_confirmed_game(), RETRIEVED_AT).away_lineup.players[0]

    assert candidate_eligible_from_lineup(confirmed) is True
    for invalid_player in (
        replace(confirmed, lineup_status="unconfirmed"),
        replace(confirmed, player_mlbam_id=None),
        replace(confirmed, player_mlbam_id=0),
        replace(confirmed, batting_order=None),
        replace(confirmed, batting_order=10),
    ):
        assert candidate_eligible_from_lineup(invalid_player) is False
