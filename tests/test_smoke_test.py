import io
import json
from pathlib import Path
from typing import Any

from hitterdna.smoke_test import main, run_schedule_smoke_test
from hitterdna.statsapi import StatsAPIClient


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
        self.calls = 0

    def get(self, *_: Any, **__: Any) -> FakeResponse:
        self.calls += 1
        return FakeResponse(self.payload)


def load_schedule() -> dict[str, Any]:
    return json.loads((FIXTURES / "schedule_2026-08-29.json").read_text())


def test_smoke_test_emits_compact_normalized_json_from_fixture() -> None:
    session = FakeSession(load_schedule())
    output = io.StringIO()

    exit_code = main(
        ["--date", "2026-08-29", "--away-team", "HOU", "--home-team", "NYM"],
        client=StatsAPIClient(session=session),
        stdout=output,
    )

    assert exit_code == 0
    assert session.calls == 1
    assert json.loads(output.getvalue()) == {
        "analysis_date": "2026-08-29",
        "games_returned": 1,
        "games": [
            {
                "game_pk": 777001,
                "away_team": "HOU",
                "home_team": "NYM",
                "venue_name": "Citi Field",
                "game_status": "Scheduled",
                "probable_away_pitcher_name": "Framber Valdez",
                "probable_home_pitcher_name": "Kodai Senga",
            }
        ],
    }


def test_smoke_test_returns_nonzero_for_no_games() -> None:
    output = io.StringIO()
    errors = io.StringIO()

    exit_code = main(
        ["--date", "2026-08-29", "--away-team", "ATL"],
        client=StatsAPIClient(session=FakeSession(load_schedule())),
        stdout=output,
        stderr=errors,
    )

    assert exit_code == 1
    assert json.loads(output.getvalue())["games_returned"] == 0
    assert "no matching games returned" in errors.getvalue()


def test_smoke_test_returns_nonzero_for_malformed_identity_fields() -> None:
    schedule = load_schedule()
    del schedule["dates"][0]["games"][0]["gamePk"]
    errors = io.StringIO()

    exit_code = main(
        ["--date", "2026-08-29"],
        client=StatsAPIClient(session=FakeSession(schedule)),
        stderr=errors,
    )

    assert exit_code == 1
    assert "malformed required identity fields: game_pk" in errors.getvalue()


def test_run_schedule_smoke_test_rejects_unnormalizable_response() -> None:
    client = StatsAPIClient(session=FakeSession({"dates": "not-a-list"}))

    try:
        run_schedule_smoke_test("2026-08-29", client=client)
    except ValueError as error:
        assert "no dates array" in str(error)
    else:
        raise AssertionError("expected malformed schedule to be rejected")
