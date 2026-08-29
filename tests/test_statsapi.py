import json
from pathlib import Path
from typing import Any

from hitterdna.statsapi import SCHEDULE_URL, StatsAPIClient


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
        self.calls: list[tuple[str, dict[str, str | int], float]] = []

    def get(
        self, url: str, *, params: dict[str, str | int], timeout: float
    ) -> FakeResponse:
        self.calls.append((url, params, timeout))
        return FakeResponse(self.payload)


def load_schedule() -> dict[str, Any]:
    return json.loads((FIXTURES / "schedule_2026-08-29.json").read_text())


def test_resolves_normalized_game_context_from_saved_schedule() -> None:
    session = FakeSession(load_schedule())
    client = StatsAPIClient(session=session)

    context = client.resolve_game_context("2026-08-29", "HOU", "NYM")

    assert context is not None
    assert context.analysis_date == "2026-08-29"
    assert context.game_pk == 777001
    assert context.venue_id == 3289
    assert context.venue_name == "Citi Field"
    assert context.away_team == "HOU"
    assert context.home_team == "NYM"
    assert context.probable_away_pitcher_mlbam_id == 605483
    assert context.probable_away_pitcher_name == "Framber Valdez"
    assert context.probable_home_pitcher_mlbam_id == 594798
    assert context.probable_home_pitcher_name == "Kodai Senga"
    assert context.game_status == "Scheduled"
    assert session.calls == [
        (
            SCHEDULE_URL,
            {"sportId": 1, "date": "2026-08-29", "hydrate": "probablePitcher,venue"},
            10.0,
        )
    ]


def test_missing_game_is_unresolved_without_live_network() -> None:
    client = StatsAPIClient(session=FakeSession(load_schedule()))

    assert client.resolve_game_context("2026-08-29", "ATL", "COL") is None
