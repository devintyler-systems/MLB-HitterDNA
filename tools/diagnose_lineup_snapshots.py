"""Read-only diagnostic report for captured MLB StatsAPI live-feed lineups."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from hitterdna.lineups import normalize_batting_order, normalize_game_lineups
from hitterdna.statsapi import normalize_schedule_contexts


def diagnose_lineup_snapshots(
    fixture_path: Path, report_path: Path, confirmed_lineups_path: Path | None = None,
) -> list[dict[str, Any]]:
    """Return one side-level, provenance-preserving row for each captured feed."""

    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    contexts = {context.game_pk: context for context in normalize_schedule_contexts(fixture["schedule"])}
    report = json.loads(report_path.read_text(encoding="utf-8"))
    reports = {
        _game_pk(row.get("endpoint_or_url")): row
        for row in report.get("sources", [])
        if row.get("endpoint_id") == "mlb_statsapi_lineup_confirmation"
    }
    provenance = fixture.get("provenance", {}).get("mlb_statsapi_lineup_confirmation", {})
    snapshots = provenance.get("raw_snapshot_by_game_pk", {})
    prior_counts = _confirmed_counts(confirmed_lineups_path)
    rows: list[dict[str, Any]] = []
    for game_pk, collection_row in sorted(reports.items()):
        if game_pk is None:
            continue
        context = contexts.get(game_pk)
        if context is None:
            continue
        prior_snapshot = snapshots.get(str(game_pk), {})
        snapshot_path = Path(collection_row.get("snapshot") or prior_snapshot.get("snapshot_path", ""))
        if not snapshot_path.is_file():
            continue
        raw = snapshot_path.read_bytes()
        payload = json.loads(raw.decode("utf-8"))
        feed_status = _mapping(_mapping(payload.get("gameData")).get("status"))
        retrieved_at = str(collection_row.get("retrieved_at_utc") or prior_snapshot.get("retrieved_at_utc", ""))
        lineups = normalize_game_lineups(context, payload, retrieved_at)
        for side in ("away", "home"):
            team = _mapping(_mapping(_mapping(payload.get("liveData")).get("boxscore")).get("teams")).get(side)
            team = _mapping(team)
            order = team.get("battingOrder")
            players = _mapping(team.get("players"))
            extracted = [_entry_detail(entry, players) for entry in order] if isinstance(order, list) else []
            lineup = getattr(lineups, f"{side}_lineup")
            rows.append({
                "game_pk": game_pk,
                "team_side": side,
                "game_status": context.game_status,
                "raw_feed_game_status": feed_status.get("detailedState") or feed_status.get("abstractGameState"),
                "scheduled_start_utc": context.scheduled_start_utc,
                "raw_snapshot_path": str(snapshot_path),
                "raw_response_hash": hashlib.sha256(raw).hexdigest(),
                "retrieved_at_utc": retrieved_at,
                "batting_order_path": f"liveData.boxscore.teams.{side}.battingOrder",
                "player_records_path": f"liveData.boxscore.teams.{side}.players.ID{{player_mlbam_id}}",
                "has_batting_order_array": isinstance(order, list),
                "batting_order_entry_count": len(order) if isinstance(order, list) else 0,
                "entries": extracted,
                "normalize_game_lineups_status": lineup.lineup_status,
                "normalize_game_lineups_players": [asdict(player) for player in lineup.players],
                "collector_parser_status": reports.get(game_pk, {}).get("parser_status"),
                "collector_attachment_status": reports.get(game_pk, {}).get("attachment_status"),
                "collector_reason": reports.get(game_pk, {}).get("reason"),
                "run_slate_confirmed_hitter_count": prior_counts.get(game_pk, {}).get(side, 0),
            })
    return rows


def _confirmed_counts(path: Path | None) -> dict[int, dict[str, int]]:
    if path is None or not path.is_file():
        return {}
    try:
        games = json.loads(path.read_text(encoding="utf-8")).get("data", {}).get("games", [])
    except (OSError, json.JSONDecodeError):
        return {}
    return {
        item["game_pk"]: {side: len(item.get(side, [])) for side in ("away", "home")}
        for item in games if isinstance(item, Mapping) and isinstance(item.get("game_pk"), int)
    }


def _entry_detail(entry: Any, players: Mapping[str, Any]) -> dict[str, Any]:
    record = _mapping(players.get(f"ID{entry}") or players.get(str(entry)))
    person = _mapping(record.get("person"))
    batting = _mapping(_mapping(record.get("stats")).get("batting"))
    return {
        "batting_order_entry": entry,
        "player_mlbam_id": person.get("id"),
        "batting_order_path": "stats.batting.battingOrder",
        "batting_order_value": batting.get("battingOrder"),
        "normalized_batting_order": normalize_batting_order(batting.get("battingOrder")),
        "has_batting_order_position": normalize_batting_order(batting.get("battingOrder")) is not None,
    }


def _game_pk(url: Any) -> int | None:
    if not isinstance(url, str):
        return None
    parts = url.rstrip("/").split("/")
    try:
        return int(parts[-3]) if parts[-2:] == ["feed", "live"] else None
    except ValueError:
        return None


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only captured-lineup diagnostic")
    parser.add_argument("--fixture", required=True, type=Path)
    parser.add_argument("--collection-report", required=True, type=Path)
    parser.add_argument("--confirmed-lineups", type=Path)
    parser.add_argument("--summary", action="store_true")
    args = parser.parse_args(argv)
    rows = diagnose_lineup_snapshots(args.fixture, args.collection_report, args.confirmed_lineups)
    if args.summary:
        rows = [{
            key: row[key]
            for key in (
                "game_pk", "team_side", "game_status", "raw_feed_game_status", "scheduled_start_utc",
                "raw_snapshot_path", "raw_response_hash", "retrieved_at_utc", "batting_order_path",
                "player_records_path", "has_batting_order_array", "batting_order_entry_count",
                "normalize_game_lineups_status", "collector_parser_status", "collector_attachment_status",
                "collector_reason", "run_slate_confirmed_hitter_count",
            )
        } | {
            "player_id_count": len([entry for entry in row["entries"] if isinstance(entry["player_mlbam_id"], int)]),
            "player_batting_order_positions_present": all(entry["has_batting_order_position"] for entry in row["entries"]),
            "lineup_extraction_rule": "array index 1..9 from liveData.boxscore.teams.{side}.battingOrder",
        } for row in rows]
    print(json.dumps(rows, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
