import io
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from hitterdna.lineups import ProbablePitcher, normalize_game_lineups
from hitterdna.slate_context import _merged_pitcher, build_slate_context, main
from hitterdna.statsapi import StatsAPIClient


FIXTURES = Path(__file__).parent / "fixtures"
RETRIEVED_AT = "2026-08-29T00:00:00Z"


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


class FixtureLineupFetcher:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.game_pks: list[int | None] = []
        self.retrieved_at_utc_values: list[str] = []

    def __call__(self, context: Any, *, retrieved_at_utc: str) -> Any:
        self.game_pks.append(context.game_pk)
        self.retrieved_at_utc_values.append(retrieved_at_utc)
        return normalize_game_lineups(context, deepcopy(self.payload), retrieved_at_utc)


def load_fixture(path: str) -> dict[str, Any]:
    return json.loads((FIXTURES / path).read_text())


def schedule_client(payload: dict[str, Any]) -> StatsAPIClient:
    return StatsAPIClient(session=FakeSession(payload))


def confirmed_feed() -> dict[str, Any]:
    payload = load_fixture("lineups/confirmed_game.json")
    payload["gameData"] = {
        "probablePitchers": {
            "away": {"id": 605483, "fullName": "Framber Valdez", "pitchHand": {"code": "L"}},
            "home": {"id": 594798, "fullName": "Kodai Senga", "pitchHand": {"code": "R"}},
        }
    }
    return payload


def game(status: str, game_pk: int) -> dict[str, Any]:
    payload = load_fixture("schedule_2026-08-29.json")["dates"][0]["games"][0]
    payload["gamePk"] = game_pk
    payload["status"]["detailedState"] = status
    return payload


def schedule_with(*games: dict[str, Any]) -> dict[str, Any]:
    return {"dates": [{"date": "2026-08-29", "games": list(games)}]}


def build(payload: dict[str, Any], fetcher: FixtureLineupFetcher | None = None) -> tuple[dict[str, Any], FixtureLineupFetcher]:
    fixture_fetcher = fetcher or FixtureLineupFetcher(confirmed_feed())
    artifact = build_slate_context(
        "2026-08-29",
        client=schedule_client(payload),
        retrieved_at_utc=RETRIEVED_AT,
        lineup_fetcher=fixture_fetcher,
    )
    return artifact, fixture_fetcher


def test_eligible_game_preserves_confirmed_lineups_venue_and_probable_pitchers() -> None:
    artifact, fetcher = build(load_fixture("schedule_2026-08-29.json"))

    assert artifact["retrieved_at_utc"] == RETRIEVED_AT
    assert [game["game_pk"] for game in artifact["games"]] == [777001]
    record = artifact["games"][0]
    assert record["freshness_state"] == "eligible_refresh"
    assert record["venue"] == {"venue_id": 3289, "venue_name": "Citi Field"}
    assert record["away_team"]["lineup_status"] == "confirmed"
    assert record["home_team"]["lineup_status"] == "confirmed"
    assert record["away_team"]["hitters"][0] == {
        "mlbam_id": 101,
        "full_name": "Away Player 1",
        "batting_order": 1,
        "position": "DH",
    }
    assert record["home_team"]["probable_pitcher"] == {
        "mlbam_id": 594798,
        "full_name": "Kodai Senga",
        "throws": "R",
        "status": "probable",
    }
    assert fetcher.game_pks == [777001]
    assert fetcher.retrieved_at_utc_values == [RETRIEVED_AT]


def test_urgent_game_is_included_and_fetches_lineup_context() -> None:
    artifact, fetcher = build(schedule_with(game("Warmup", 777010)))

    assert artifact["games"][0]["freshness_state"] == "urgent_refresh"
    assert artifact["games"][0]["away_team"]["lineup_status"] == "confirmed"
    assert fetcher.game_pks == [777010]


@pytest.mark.parametrize(
    ("status", "freshness_state"),
    [("In Progress", "exclude_in_progress"), ("Final", "exclude_terminal")],
)
def test_excluded_game_states_are_emitted_without_game_feed_fetch(
    status: str, freshness_state: str
) -> None:
    artifact, fetcher = build(schedule_with(game(status, 777020)))

    record = artifact["games"][0]
    assert record["freshness_state"] == freshness_state
    assert record["exclusion_reason"] == "game freshness state excludes pregame lineup refresh"
    assert record["away_team"]["lineup_status"] == "not_fetched"
    assert record["away_team"]["hitters"] == []
    assert fetcher.game_pks == []


def test_unknown_game_state_is_held_without_game_feed_fetch() -> None:
    artifact, fetcher = build(schedule_with(game("Delayed", 777030)))

    record = artifact["games"][0]
    assert record["freshness_state"] == "hold_unknown"
    assert record["exclusion_reason"] == "game freshness state is unknown; pregame lineup refresh is held"
    assert record["home_team"]["lineup_status"] == "not_fetched"
    assert fetcher.game_pks == []


def test_unconfirmed_lineup_never_exposes_partial_hitters() -> None:
    feed = confirmed_feed()
    feed["liveData"]["boxscore"]["teams"]["away"]["battingOrder"].pop()
    artifact, _ = build(load_fixture("schedule_2026-08-29.json"), FixtureLineupFetcher(feed))

    away_team = artifact["games"][0]["away_team"]
    assert away_team["lineup_status"] == "unconfirmed"
    assert away_team["lineup_reason"] == (
        "lineup does not contain exactly nine unique players in batting-order slots 1 through 9"
    )
    assert away_team["hitters"] == []


def test_doubleheader_retains_two_distinct_game_pks_without_team_date_deduplication() -> None:
    artifact, fetcher = build(schedule_with(game("Scheduled", 777101), game("Pre-Game", 777102)))

    assert [record["game_pk"] for record in artifact["games"]] == [777101, 777102]
    assert fetcher.game_pks == [777101, 777102]
    assert fetcher.retrieved_at_utc_values == [RETRIEVED_AT, RETRIEVED_AT]


def test_missing_pitcher_fields_remain_null_without_name_resolution() -> None:
    payload = schedule_with(game("In Progress", 777040))
    teams = payload["dates"][0]["games"][0]["teams"]
    teams["away"]["probablePitcher"] = {"fullName": "Name Without Identifier"}
    teams["home"]["probablePitcher"] = {"id": 594798}
    artifact, fetcher = build(payload)

    away_pitcher = artifact["games"][0]["away_team"]["probable_pitcher"]
    home_pitcher = artifact["games"][0]["home_team"]["probable_pitcher"]
    assert away_pitcher == {
        "mlbam_id": None,
        "full_name": "Name Without Identifier",
        "throws": None,
        "status": "unresolved",
    }
    assert home_pitcher == {
        "mlbam_id": 594798,
        "full_name": None,
        "throws": None,
        "status": "probable",
    }
    assert fetcher.game_pks == []


def test_merged_pitcher_selects_feed_record_with_valid_id_without_field_merging() -> None:
    schedule_pitcher = ProbablePitcher(100001, "Schedule Name", "L", "probable")
    feed_pitcher = ProbablePitcher(200002, "Feed Name", "R", "probable")

    assert _merged_pitcher(schedule_pitcher, feed_pitcher) is feed_pitcher


def test_merged_pitcher_selects_valid_schedule_record_over_name_only_feed() -> None:
    schedule_pitcher = ProbablePitcher(100001, "Schedule Name", "L", "probable")
    feed_pitcher = ProbablePitcher(None, "Feed Name", "R", "unresolved")

    assert _merged_pitcher(schedule_pitcher, feed_pitcher) is schedule_pitcher


def test_merged_pitcher_keeps_name_only_feed_record_atomic_when_no_ids_exist() -> None:
    schedule_pitcher = ProbablePitcher(None, "Schedule Name", "L", "unresolved")
    feed_pitcher = ProbablePitcher(None, "Feed Name", "R", "unresolved")

    selected = _merged_pitcher(schedule_pitcher, feed_pitcher)

    assert selected is feed_pitcher
    assert selected.status == "unresolved"
    assert selected.player_name == "Feed Name"
    assert selected.throws == "R"


def test_merged_pitcher_keeps_schedule_record_unchanged_without_feed() -> None:
    schedule_pitcher = ProbablePitcher(100001, "Schedule Name", "L", "probable")

    assert _merged_pitcher(schedule_pitcher, None) is schedule_pitcher


def test_output_contract_is_deterministic_and_omits_prohibited_exact_keys() -> None:
    artifact, _ = build(load_fixture("schedule_2026-08-29.json"))

    assert artifact["artifact_type"] == "slate_context"
    assert artifact["game_date"] == "2026-08-29"
    assert artifact["retrieved_at_utc"] == RETRIEVED_AT
    assert isinstance(artifact["games"], list)
    assert len({record["game_pk"] for record in artifact["games"]}) == len(artifact["games"])
    assert _all_keys(artifact).isdisjoint(
        {
            "probability", "projection", "market", "odds", "line", "edge", "recommendation",
            "stake", "unit", "devig", "xBA", "xSLG", "xwOBA", "expected_batting_average",
            "expected_slugging", "expected_woba",
        }
    )


def test_cli_requires_valid_date_and_explicit_output(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        main(["--date", "2026-08-99", "--output", str(tmp_path / "artifact.json")])
    with pytest.raises(SystemExit):
        main(["--date", "2026-08-29"])


def test_cli_writes_only_the_explicit_output_path_and_creates_its_parent(tmp_path: Path) -> None:
    output_path = tmp_path / "nested" / "slate.json"
    fetcher = FixtureLineupFetcher(confirmed_feed())
    errors = io.StringIO()

    exit_code = main(
        ["--date", "2026-08-29", "--output", str(output_path)],
        client=schedule_client(load_fixture("schedule_2026-08-29.json")),
        retrieved_at_utc=RETRIEVED_AT,
        lineup_fetcher=fetcher,
        stderr=errors,
    )

    assert exit_code == 0
    assert errors.getvalue() == ""
    assert json.loads(output_path.read_text())["artifact_type"] == "slate_context"
    assert list(tmp_path.iterdir()) == [output_path.parent]
    assert list(output_path.parent.iterdir()) == [output_path]


def _all_keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        return set(value).union(*(_all_keys(child) for child in value.values()))
    if isinstance(value, list):
        return set().union(*(_all_keys(child) for child in value))
    return set()
