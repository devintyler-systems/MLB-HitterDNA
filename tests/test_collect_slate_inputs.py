import hashlib
import json
from pathlib import Path

import pytest

import hitterdna.collect_slate_inputs as collector
import hitterdna.run_slate as run_slate
from hitterdna.collect_slate_inputs import SlateCollectionError, collect_slate_inputs
from hitterdna.prepare_slate_fixture import prepare_slate_fixture
from hitterdna.run_slate import build_slate_run, write_slate_run
from hitterdna.savant_expected_stats import expected_stats_source_url


ROOT = Path(__file__).parents[1]


class FakeResponse:
    def __init__(self, status_code: int, content: bytes) -> None:
        self.status_code = status_code
        self.content = content


class FakeTransport:
    def __init__(self, responses: dict[str, FakeResponse | Exception]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict | None]] = []

    def get(self, url: str, *, params: dict | None, timeout: float) -> FakeResponse:
        self.calls.append((url, params))
        response = self.responses[url]
        if isinstance(response, Exception):
            raise response
        return response


def _schedule(game_date: str = "2030-01-01") -> dict:
    return {
        "copyright": "fixture", "totalItems": 1, "totalGames": 1,
        "dates": [{"date": game_date, "games": [{
            "gamePk": 700001, "gameDate": f"{game_date}T20:00:00Z",
            "status": {"detailedState": "Scheduled"}, "venue": {"id": 1, "name": "Park"},
            "teams": {
                "away": {"team": {"id": 1, "name": "Away", "abbreviation": "AWY"}, "probablePitcher": {"id": 501}},
                "home": {"team": {"id": 2, "name": "Home", "abbreviation": "HOM"}, "probablePitcher": {"id": 502}},
            },
        }]}],
    }


def _team(team_id: int, abbreviation: str, start_id: int, actual_id: int | None = None) -> dict:
    players = {
        f"ID{player_id}": {
            "person": {"id": player_id, "fullName": f"Player {player_id}"},
            "stats": {"batting": {"battingOrder": order * 100}}, "position": {"abbreviation": "OF"},
        }
        for order, player_id in enumerate(range(start_id, start_id + 9), start=1)
    }
    if actual_id is not None:
        players[f"ID{actual_id}"] = {"person": {"id": actual_id}, "stats": {"pitching": {"gamesStarted": 1}}}
    return {"team": {"id": team_id, "abbreviation": abbreviation}, "battingOrder": list(range(start_id, start_id + 9)), "players": players}


def _live_feed(*, partial: bool = False, actual_id: int | None = 503, game_status: str | None = None) -> bytes:
    away = _team(1, "AWY", 101, actual_id)
    home = _team(2, "HOM", 201, 504)
    if partial:
        away["battingOrder"] = away["battingOrder"][:8]
    payload = {"liveData": {"boxscore": {"teams": {"away": away, "home": home}}}}
    if game_status is not None:
        payload["gameData"] = {"status": {"detailedState": game_status}}
    return json.dumps(payload).encode()


def _savant() -> bytes:
    return b"player_id,player_name,year,pa,est_woba\n101,Player 101,2030,100,0.400\n"


def _captured_savant() -> bytes:
    return (ROOT / "tests" / "fixtures" / "savant_expected_stats" / "captured_expected_statistics_2026.csv").read_bytes()


def _fixture(tmp_path: Path, game_date: str = "2030-01-01") -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    raw_path = tmp_path / "schedule.json"
    raw_path.write_bytes(json.dumps(_schedule(game_date)).encode())
    fixture_path = tmp_path / "slate.json"
    fixture_path.write_text(json.dumps(prepare_slate_fixture(
        game_date, raw_path, retrieved_at_utc="2030-01-01T00:00:00Z",
    )), encoding="utf-8")
    return fixture_path


def _healthy_transport() -> FakeTransport:
    feed_url = "https://statsapi.mlb.com/api/v1.1/game/700001/feed/live"
    return FakeTransport({feed_url: FakeResponse(200, _live_feed()), expected_stats_source_url(2030): FakeResponse(200, _savant())})


def test_collection_preserves_exact_raw_bytes_hashes_and_canonical_ids(tmp_path: Path) -> None:
    fixture_path = _fixture(tmp_path)
    transport = _healthy_transport()
    manifest, report = collect_slate_inputs("2030-01-01", fixture_path, tmp_path / "snapshots", transport=transport, retrieved_at_utc="2030-01-01T01:00:00Z")
    fixture = json.loads(fixture_path.read_text())
    lineup = fixture["provenance"]["mlb_statsapi_lineup_confirmation"]
    savant = fixture["provenance"]["savant_expected_statistics"]
    assert report["status"] == "PASS"
    savant_report = next(row for row in report["sources"] if row["endpoint_id"] == "savant_expected_statistics")
    assert savant_report["fetch_status"] == savant_report["parser_status"] == savant_report["attachment_status"] == "PASS"
    assert lineup["canonical_keys"] == {"game_pk": [700001], "player_mlbam_id": list(range(101, 110)) + list(range(201, 210))}
    assert savant["canonical_keys"] == {"player_mlbam_id": [101], "season": 2030}
    assert len(lineup["raw_response_hash"]) == 64
    assert lineup["raw_response_hash_by_game_pk"] == {"700001": lineup["raw_response_hash"]}
    assert lineup["raw_snapshot_by_game_pk"]["700001"]["raw_response_hash"] == lineup["raw_response_hash"]
    snapshots = [path for path in (tmp_path / "snapshots").glob("*.raw")]
    assert snapshots
    assert any(path.read_bytes() == _live_feed() for path in snapshots)
    assert any(hashlib.sha256(path.read_bytes()).hexdigest() == lineup["raw_response_hash"] for path in snapshots if b"liveData" in path.read_bytes())
    assert manifest["sources"][0]["action"] == "reused_healthy_fixture"
    assert {url for url, _ in transport.calls} == {
        "https://statsapi.mlb.com/api/v1.1/game/700001/feed/live", expected_stats_source_url(2030),
    }


def test_confirmed_and_partial_live_feeds_attach_only_official_confirmation(tmp_path: Path) -> None:
    fixture_path = _fixture(tmp_path)
    transport = _healthy_transport()
    collect_slate_inputs("2030-01-01", fixture_path, tmp_path / "snapshots", transport=transport, retrieved_at_utc="2030-01-01T01:00:00Z")
    output = tmp_path / "output"
    write_slate_run(build_slate_run("2030-01-01", fixture_path), output)
    lineups = json.loads((output / "confirmed_lineups.json").read_text())["data"]["games"][0]
    starters = json.loads((output / "starter_context.json").read_text())["data"]["games"][0]["starters"]
    assert len(lineups["away"]) == len(lineups["home"]) == 9
    assert starters[0]["selected"]["kind"] == "actual"
    assert starters[0]["selected"]["player_mlbam_id"] == 503
    source_rows = [json.loads(line) for line in (output / "source_observations.jsonl").read_text().splitlines()]
    lineup_rows = [row for row in source_rows if row["endpoint_id"] == "mlb_statsapi_lineup_confirmation"]
    assert len(lineup_rows) == 1
    assert lineup_rows[0]["provenance"]["canonical_keys"] == {"game_pk": 700001}
    assert len(lineup_rows[0]["provenance"]["raw_response_hash"]) == 64

    partial_fixture = _fixture(tmp_path / "partial")
    feed_url = "https://statsapi.mlb.com/api/v1.1/game/700001/feed/live"
    partial_transport = FakeTransport({feed_url: FakeResponse(200, _live_feed(partial=True)), expected_stats_source_url(2030): FakeResponse(503, b"unavailable")})
    collect_slate_inputs("2030-01-01", partial_fixture, tmp_path / "partial-snapshots", transport=partial_transport, retrieved_at_utc="2030-01-01T01:00:00Z")
    partial_output = tmp_path / "partial-output"
    write_slate_run(build_slate_run("2030-01-01", partial_fixture), partial_output)
    partial_lineups = json.loads((partial_output / "confirmed_lineups.json").read_text())["data"]["games"][0]
    assert partial_lineups["away"] == []
    assert len(partial_lineups["home"]) == 9


def test_incomplete_live_feed_is_explicitly_unverified_without_confirmation(tmp_path: Path) -> None:
    fixture_path = _fixture(tmp_path)
    feed_url = "https://statsapi.mlb.com/api/v1.1/game/700001/feed/live"
    transport = FakeTransport({
        feed_url: FakeResponse(200, _live_feed(partial=True)),
        expected_stats_source_url(2030): FakeResponse(200, _savant()),
    })

    _, report = collect_slate_inputs(
        "2030-01-01", fixture_path, tmp_path / "snapshots", transport=transport,
        retrieved_at_utc="2030-01-01T01:00:00Z",
    )
    fixture = json.loads(fixture_path.read_text())
    lineup_report = next(row for row in report["sources"] if row["endpoint_id"] == "mlb_statsapi_lineup_confirmation")

    assert lineup_report["fetch_status"] == "PASS"
    assert lineup_report["parser_status"] == "PASS"
    assert lineup_report["attachment_status"] == "PARTIAL"
    assert lineup_report["reason"] == "partial_official_batting_order"
    assert lineup_report["lineup_side_statuses"] == {"away": "UNCONFIRMED", "home": "CONFIRMED"}
    assert fixture["provenance"]["mlb_statsapi_lineup_confirmation"]["source_failure_status"] == "PASS"
    assert set(fixture["lineup_payloads"]["700001"]) == {"home"}


def test_stale_actual_never_replaces_healthy_actual_or_probable_evidence(tmp_path: Path) -> None:
    fixture = {"starter_evidence": [{"game_pk": 1, "team_side": "away", "kind": "actual", "player_mlbam_id": 503, "source_freshness_status": "PASS", "source_failure_status": "PASS"}]}
    collector._merge_starter_evidence(fixture, [{"game_pk": 1, "team_side": "away", "kind": "actual", "player_mlbam_id": 504, "source_freshness_status": "STALE", "source_failure_status": "PASS"}])
    assert fixture["starter_evidence"][0]["player_mlbam_id"] == 503


def test_stale_actual_evidence_does_not_override_a_healthy_probable_in_runner(tmp_path: Path) -> None:
    fixture_path = _fixture(tmp_path)
    raw = json.loads(fixture_path.read_text())
    raw["provenance"]["mlb_statsapi_probable_pitchers"] = dict(raw["provenance"]["mlb_statsapi_schedule_by_date"])
    raw["provenance"]["mlb_statsapi_probable_pitchers"]["canonical_keys"] = {"game_pk": [700001]}
    raw["starter_evidence"] = [
        {"game_pk": 700001, "team_side": "away", "kind": "probable", "player_mlbam_id": 501, "source_freshness_status": "PASS", "source_failure_status": "PASS"},
        {"game_pk": 700001, "team_side": "away", "kind": "actual", "player_mlbam_id": 503, "source_freshness_status": "STALE", "source_failure_status": "PASS"},
    ]
    raw["unavailable_source_inputs"].pop("mlb_statsapi_probable_pitchers")
    fixture_path.write_text(json.dumps(raw), encoding="utf-8")
    starter = build_slate_run("2030-01-01", fixture_path)["starter_context"]["data"]["games"][0]["starters"][0]
    assert starter["selected"]["kind"] == "probable"
    assert starter["selected"]["player_mlbam_id"] == 501


def test_stale_schedule_is_collected_through_the_authorized_schedule_route(tmp_path: Path) -> None:
    fixture_path = _fixture(tmp_path)
    raw = json.loads(fixture_path.read_text())
    raw["provenance"]["mlb_statsapi_schedule_by_date"]["source_freshness_status"] = "STALE"
    fixture_path.write_text(json.dumps(raw), encoding="utf-8")
    schedule_url = "https://statsapi.mlb.com/api/v1/schedule"
    feed_url = "https://statsapi.mlb.com/api/v1.1/game/700001/feed/live"
    transport = FakeTransport({
        schedule_url: FakeResponse(200, json.dumps(_schedule()).encode()),
        feed_url: FakeResponse(200, _live_feed()),
        expected_stats_source_url(2030): FakeResponse(200, _savant()),
    })
    collect_slate_inputs("2030-01-01", fixture_path, tmp_path / "snapshots", transport=transport, retrieved_at_utc="2030-01-01T01:00:00Z")
    assert transport.calls[0] == (schedule_url, {"sportId": 1, "date": "2030-01-01", "hydrate": "probablePitcher,venue"})
    updated = json.loads(fixture_path.read_text())
    assert updated["provenance"]["mlb_statsapi_schedule_by_date"]["source_freshness_status"] == "PASS"


def test_unhealthy_savant_is_unverified_and_cannot_create_usable_rows(tmp_path: Path) -> None:
    fixture_path = _fixture(tmp_path)
    feed_url = "https://statsapi.mlb.com/api/v1.1/game/700001/feed/live"
    transport = FakeTransport({feed_url: FakeResponse(200, _live_feed()), expected_stats_source_url(2030): FakeResponse(503, b"down")})
    _, report = collect_slate_inputs("2030-01-01", fixture_path, tmp_path / "snapshots", transport=transport, retrieved_at_utc="2030-01-01T01:00:00Z")
    fixture = json.loads(fixture_path.read_text())
    assert report["status"] == "RETRYABLE"
    assert "savant_expected_statistics" not in fixture["provenance"]
    assert fixture["unavailable_source_inputs"]["savant_expected_statistics"]["status"] == "UNVERIFIED"
    output = tmp_path / "output"
    write_slate_run(build_slate_run("2030-01-01", fixture_path), output)
    assert json.loads((output / "expected_statistics.json").read_text())["data"]["rows"] == []


def test_transport_success_parser_invalid_savant_preserves_raw_and_stays_unverified(tmp_path: Path) -> None:
    fixture_path = _fixture(tmp_path)
    feed_url = "https://statsapi.mlb.com/api/v1.1/game/700001/feed/live"
    invalid = b"not,a,valid,expected,statistics\nstill,not,a,valid,row\n"
    transport = FakeTransport({feed_url: FakeResponse(200, _live_feed()), expected_stats_source_url(2030): FakeResponse(200, invalid)})
    _, report = collect_slate_inputs("2030-01-01", fixture_path, tmp_path / "snapshots", transport=transport, retrieved_at_utc="2030-01-01T01:00:00Z")
    fixture = json.loads(fixture_path.read_text())
    savant_report = next(row for row in report["sources"] if row["endpoint_id"] == "savant_expected_statistics")
    assert savant_report["fetch_status"] == "PASS"
    assert savant_report["parser_status"] == savant_report["attachment_status"] == savant_report["status"] == "UNVERIFIED"
    assert savant_report["reason"] == "parser_schema_mismatch: payload has no structurally valid rows"
    assert any(path.read_bytes() == invalid for path in (tmp_path / "snapshots").glob("*.raw"))
    assert "expected_statistics" not in fixture
    assert fixture["unavailable_source_inputs"]["savant_expected_statistics"]["status"] == "UNVERIFIED"
    output = tmp_path / "output"
    write_slate_run(build_slate_run("2030-01-01", fixture_path), output)
    assert json.loads((output / "expected_statistics.json").read_text())["data"]["rows"] == []
    report_output = json.loads((output / "validation_report.json").read_text())
    savant_state = next(row for row in report_output["data"]["source_contracts"] if row["endpoint_id"] == "savant_expected_statistics")
    assert savant_state["retrieval_status"] == "UNVERIFIED"


def test_exact_captured_savant_attaches_only_after_parser_validation(tmp_path: Path) -> None:
    fixture_path = _fixture(tmp_path, "2026-09-07")
    feed_url = "https://statsapi.mlb.com/api/v1.1/game/700001/feed/live"
    raw = _captured_savant()
    transport = FakeTransport({
        feed_url: FakeResponse(200, _live_feed()),
        expected_stats_source_url(2026): FakeResponse(200, raw),
    })

    _, report = collect_slate_inputs(
        "2026-09-07", fixture_path, tmp_path / "snapshots", transport=transport,
        retrieved_at_utc="2030-01-01T01:00:00Z",
    )
    savant_report = next(row for row in report["sources"] if row["endpoint_id"] == "savant_expected_statistics")
    assert savant_report["fetch_status"] == "PASS"
    assert savant_report["parser_status"] == savant_report["attachment_status"] == "PASS"
    fixture = json.loads(fixture_path.read_text())
    assert fixture["provenance"]["savant_expected_statistics"]["canonical_keys"] == {
        "player_mlbam_id": [608324, 691718], "season": 2026,
    }
    assert any(path.read_bytes() == raw for path in (tmp_path / "snapshots").glob("*.raw"))


def test_multi_game_provenance_keeps_individual_raw_hashes(tmp_path: Path) -> None:
    first_bytes, second_bytes = b"first", b"second"
    first_path = collector._write_snapshot(tmp_path, "live-feed-1", first_bytes, hashlib.sha256(first_bytes).hexdigest())
    second_path = collector._write_snapshot(tmp_path, "live-feed-2", second_bytes, hashlib.sha256(second_bytes).hexdigest())
    responses = [
        collector.CollectedResponse("mlb_statsapi_lineup_confirmation", "https://statsapi.mlb.com/api/v1.1/game/1/feed/live", {}, "2030-01-01T01:00:00Z", "PASS", "PASS", first_bytes, hashlib.sha256(first_bytes).hexdigest(), first_path, None),
        collector.CollectedResponse("mlb_statsapi_lineup_confirmation", "https://statsapi.mlb.com/api/v1.1/game/2/feed/live", {}, "2030-01-01T01:00:00Z", "PASS", "PASS", second_bytes, hashlib.sha256(second_bytes).hexdigest(), second_path, None),
    ]
    provenance = collector._provenance_from_many(responses, "MLB Stats API", 0, {"game_pk": [1, 2], "player_mlbam_id": []}, {"1": responses[0].raw_response_hash, "2": responses[1].raw_response_hash})
    assert provenance["raw_response_hash"] == responses[0].raw_response_hash
    assert provenance["raw_response_hash_by_game_pk"] == {"1": responses[0].raw_response_hash, "2": responses[1].raw_response_hash}
    assert not provenance["raw_response_hash"].startswith("{")
    rows = run_slate._contributing_source_observations({"mlb_statsapi_lineup_confirmation": provenance})
    assert [row["provenance"]["raw_response_hash"] for row in rows] == [responses[0].raw_response_hash, responses[1].raw_response_hash]
    assert [row["provenance"]["canonical_keys"] for row in rows] == [{"game_pk": 1}, {"game_pk": 2}]


def test_repeated_collection_does_not_replace_healthy_inputs_with_failures(tmp_path: Path) -> None:
    fixture_path = _fixture(tmp_path)
    collect_slate_inputs("2030-01-01", fixture_path, tmp_path / "snapshots", transport=_healthy_transport(), retrieved_at_utc="2030-01-01T01:00:00Z")
    before = json.loads(fixture_path.read_text())
    feed_url = "https://statsapi.mlb.com/api/v1.1/game/700001/feed/live"
    failures = FakeTransport({feed_url: FakeResponse(503, b"down"), expected_stats_source_url(2030): FakeResponse(503, b"down")})
    collect_slate_inputs("2030-01-01", fixture_path, tmp_path / "snapshots", transport=failures, retrieved_at_utc="2030-01-01T02:00:00Z")
    after = json.loads(fixture_path.read_text())
    for endpoint_id in ("mlb_statsapi_lineup_confirmation", "savant_expected_statistics"):
        assert after["provenance"][endpoint_id] == before["provenance"][endpoint_id]


def test_later_complete_feed_upgrades_partial_side_and_later_partial_or_malformed_never_downgrades(tmp_path: Path) -> None:
    fixture_path = _fixture(tmp_path)
    feed_url = "https://statsapi.mlb.com/api/v1.1/game/700001/feed/live"
    collect_slate_inputs(
        "2030-01-01", fixture_path, tmp_path / "snapshots",
        transport=FakeTransport({feed_url: FakeResponse(200, _live_feed(partial=True)), expected_stats_source_url(2030): FakeResponse(200, _savant())}),
        retrieved_at_utc="2030-01-01T01:00:00Z",
    )
    collect_slate_inputs(
        "2030-01-01", fixture_path, tmp_path / "snapshots", transport=_healthy_transport(),
        retrieved_at_utc="2030-01-01T02:00:00Z",
    )
    upgraded = json.loads(fixture_path.read_text())
    selected = upgraded["lineup_payloads"]["700001"]
    assert set(selected) == {"away", "home"}
    away_before = selected["away"]
    collect_slate_inputs(
        "2030-01-01", fixture_path, tmp_path / "snapshots",
        transport=FakeTransport({feed_url: FakeResponse(200, b"{}"), expected_stats_source_url(2030): FakeResponse(503, b"down")}),
        retrieved_at_utc="2030-01-01T03:00:00Z",
    )
    after = json.loads(fixture_path.read_text())
    assert after["lineup_payloads"]["700001"]["away"] == away_before
    assert len(after["lineup_observations"]) == 3
    lineups = build_slate_run("2030-01-01", fixture_path)["confirmed_lineups"]["data"]["games"][0]
    assert len(lineups["away"]) == len(lineups["home"]) == 9


def test_started_game_retains_confirmed_lineup_but_has_no_pregame_candidates(tmp_path: Path) -> None:
    fixture_path = _fixture(tmp_path)
    feed_url = "https://statsapi.mlb.com/api/v1.1/game/700001/feed/live"
    collect_slate_inputs(
        "2030-01-01", fixture_path, tmp_path / "snapshots",
        transport=FakeTransport({feed_url: FakeResponse(200, _live_feed(game_status="In Progress")), expected_stats_source_url(2030): FakeResponse(200, _savant())}),
        retrieved_at_utc="2030-01-01T01:00:00Z",
    )
    artifacts = build_slate_run("2030-01-01", fixture_path)
    assert len(artifacts["confirmed_lineups"]["data"]["games"][0]["away"]) == 9
    assert artifacts["discovery_queue"]["data"]["records"] == []
    assert {record["reason"] for record in artifacts["exclusions"]["data"]["records"]} == {
        "GAME_STARTED: official lineup evidence is retained but pregame discovery is ineligible"
    }


def test_registry_blocked_endpoint_never_invokes_transport(tmp_path: Path) -> None:
    context = collector.normalize_schedule_contexts(_schedule())[0]
    transport = FakeTransport({})
    blocked = type("Entry", (), {"implementation_status": "NON_AUTOMATED"})()
    with pytest.raises(SlateCollectionError, match="registry-blocked"):
        collector._collect_live_feeds((context,), tmp_path, transport, {"mlb_statsapi_lineup_confirmation": blocked}, "2030-01-01T01:00:00Z")
    assert transport.calls == []


def test_malformed_fixture_fails_before_transport_and_tests_use_only_fake_transport(tmp_path: Path) -> None:
    fixture_path = tmp_path / "bad.json"
    fixture_path.write_text("{}", encoding="utf-8")
    transport = FakeTransport({})
    with pytest.raises(SlateCollectionError):
        collect_slate_inputs("2030-01-01", fixture_path, tmp_path / "snapshots", transport=transport)
    assert transport.calls == []
