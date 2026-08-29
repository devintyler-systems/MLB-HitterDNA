import json
from pathlib import Path
from typing import Any

from hitterdna.smoke_test import run_schedule_smoke_test
from hitterdna.statsapi import StatsAPIClient, classify_pregame_eligibility


FIXTURES = Path(__file__).parent / "fixtures"


class FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self.payload


class FakeSession:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def get(self, *_: Any, **__: Any) -> FakeResponse:
        return FakeResponse(self.payload)


def load_fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text())


def test_pregame_eligibility_classifies_all_saved_statuses() -> None:
    statuses = load_fixture("pregame_eligibility_statuses.json")

    for status in statuses:
        assert classify_pregame_eligibility(status["game_status"]) == status[
            "pregame_eligibility"
        ]


def test_same_date_team_pair_doubleheader_retains_each_game_pk() -> None:
    result = run_schedule_smoke_test(
        "2026-08-29",
        away_team="HOU",
        home_team="NYM",
        client=StatsAPIClient(session=FakeSession(load_fixture("schedule_doubleheader.json"))),
    )

    assert result["games_returned"] == 2
    assert [game["game_pk"] for game in result["games"]] == [777101, 777102]
    assert [game["pregame_eligibility"] for game in result["games"]] == [
        "eligible_refresh",
        "eligible_refresh",
    ]
