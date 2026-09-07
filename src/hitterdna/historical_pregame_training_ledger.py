"""Pure local builder for the Historical Pregame Training Ledger v0."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from hitterdna.lineups import normalize_game_lineups
from hitterdna.statsapi import GameContext, normalize_schedule_contexts


SCHEMA_VERSION = "historical-pregame-training-ledger-v0"
TARGET_NAME = "official_hit_ge_1"
FEATURE_CONSTRUCTION_VERSION = "historical-pregame-ledger-v0.1"
HEALTH = {"PASS", "STALE", "UNVERIFIED", "RETRYABLE", "TERMINAL"}


def build_historical_pregame_training_ledger(captured: Mapping[str, Any]) -> dict[str, Any]:
    """Build an auditable ledger solely from explicit captured payloads."""

    generated_at = _text(captured.get("generated_at_utc"))
    schedule = captured.get("schedule")
    schedule_provenance = captured.get("schedule_provenance")
    snapshots = captured.get("pregame_snapshots")
    finals = captured.get("final_box_scores")
    if not generated_at or not isinstance(schedule, Mapping) or not isinstance(snapshots, list) or not isinstance(finals, list):
        raise ValueError("captured ledger input is malformed")
    contexts = {context.game_pk: context for context in normalize_schedule_contexts(dict(schedule)) if context.game_pk}
    schedule_health = _health_reason(schedule_provenance)
    final_by_game = {item.get("game_pk"): item for item in finals if isinstance(item, Mapping) and _positive(item.get("game_pk"))}
    rows: list[dict[str, Any]] = []
    exclusions: list[dict[str, Any]] = []
    for snapshot in snapshots:
        if not isinstance(snapshot, Mapping):
            exclusions.append(_exclusion(None, None, None, "UNVERIFIED", "MALFORMED_SOURCE_EVIDENCE"))
            continue
        game_pk = _positive(snapshot.get("game_pk"))
        context = contexts.get(game_pk)
        if context is None:
            exclusions.append(_exclusion(game_pk, None, None, "UNVERIFIED", "MISMATCHED_GAME_PK"))
            continue
        if schedule_health:
            exclusions.append(_exclusion(game_pk, None, None, _status_for_reason(schedule_health), schedule_health))
            continue
        timing = _timing_reason(snapshot, context.scheduled_start_utc)
        health = _health_reason(snapshot.get("provenance"))
        if timing or health:
            exclusions.append(_exclusion(game_pk, None, None, _status_for_reason(timing or health), timing or health))
            continue
        final = final_by_game.get(game_pk)
        if not isinstance(final, Mapping):
            exclusions.append(_exclusion(game_pk, None, None, "UNVERIFIED", "ABSENT_FINAL_OFFICIAL_LABEL"))
            continue
        final_health = _health_reason(final.get("provenance"))
        if final_health:
            exclusions.append(_exclusion(game_pk, None, None, _status_for_reason(final_health), final_health))
            continue
        if _positive(final.get("game_pk")) != game_pk:
            exclusions.append(_exclusion(game_pk, None, None, "UNVERIFIED", "MISMATCHED_GAME_PK"))
            continue
        payload = snapshot.get("payload")
        final_payload = final.get("payload")
        if not isinstance(payload, Mapping) or not isinstance(final_payload, Mapping):
            exclusions.append(_exclusion(game_pk, None, None, "UNVERIFIED", "MALFORMED_SOURCE_EVIDENCE"))
            continue
        if _payload_game_pk(payload) not in {None, game_pk} or _payload_game_pk(final_payload) not in {None, game_pk}:
            exclusions.append(_exclusion(game_pk, None, None, "UNVERIFIED", "MISMATCHED_GAME_PK"))
            continue
        lineups = normalize_game_lineups(context, dict(payload), str(snapshot["provenance"]["retrieved_at_utc"]))
        labels = _final_labels(final_payload)
        if labels is None:
            exclusions.append(_exclusion(game_pk, None, None, "UNVERIFIED", "MALFORMED_SOURCE_EVIDENCE"))
            continue
        starter_by_side, starter_exclusions = _pregame_starters(snapshot, context)
        exclusions.extend(starter_exclusions)
        confirmed_ids: set[int] = set()
        for side in ("away", "home"):
            lineup = getattr(lineups, f"{side}_lineup")
            if lineup.lineup_status != "confirmed":
                reason = "CANONICAL_PLAYER_ID_MISMATCH" if _lineup_id_mismatch(payload, side) else "PARTIAL_OR_MISSING_OFFICIAL_LINEUP"
                exclusions.append(_exclusion(game_pk, None, side, "UNVERIFIED", reason))
                continue
            opponent_side = "home" if side == "away" else "away"
            for player in lineup.players:
                if not _positive(player.player_mlbam_id) or not isinstance(player.batting_order, int) or player.batting_order not in range(1, 10):
                    exclusions.append(_exclusion(game_pk, player.player_mlbam_id, side, "UNVERIFIED", "CANONICAL_PLAYER_ID_MISMATCH"))
                    continue
                confirmed_ids.add(player.player_mlbam_id)
                hits = labels.get(player.player_mlbam_id)
                if hits is None:
                    exclusions.append(_exclusion(game_pk, player.player_mlbam_id, side, "UNVERIFIED", "ABSENT_FINAL_OFFICIAL_LABEL"))
                    continue
                rows.append({
                    "game_pk": game_pk, "game_date": context.analysis_date, "season": int(str(context.analysis_date)[:4]),
                    "player_mlbam_id": player.player_mlbam_id, "team_mlbam_id": player.team_id,
                    "opponent_team_mlbam_id": getattr(context, f"{opponent_side}_team_id"), "venue_mlbam_id": context.venue_id,
                    "opponent_starter_mlbam_id": starter_by_side.get(opponent_side),
                    "scheduled_first_pitch_utc": context.scheduled_start_utc, "as_of_utc": snapshot["as_of_utc"],
                    "confirmed_lineup_side": side, "batting_order_slot": player.batting_order,
                    "official_hit_ge_1": hits >= 1, "final_label_provenance": final["provenance"],
                    "paired_pregame_snapshot_provenance": snapshot["provenance"],
                    "source_health": {"schedule": _health_status(schedule_provenance), "pregame_snapshot": "PASS", "final_label": "PASS"},
                    "feature_construction_version": FEATURE_CONSTRUCTION_VERSION,
                    "no_probability_status": "NO_PROBABILITY_CALCULATED", "model_publication_status": "MODEL_NOT_IMPLEMENTED",
                })
        for player_id in sorted(labels):
            if player_id not in confirmed_ids:
                exclusions.append(_exclusion(game_pk, player_id, None, "UNVERIFIED", "FINAL_HITTER_NOT_IN_PREGAME_CONFIRMED_LINEUP"))
    rows.sort(key=lambda row: (row["game_pk"], row["confirmed_lineup_side"], row["batting_order_slot"], row["player_mlbam_id"]))
    exclusions.sort(key=lambda row: (row["game_pk"] or 0, row["team_side"] or "", row["player_mlbam_id"] or 0, row["reason"]))
    return {
        "artifact_type": "historical_pregame_training_ledger", "schema_version": SCHEMA_VERSION,
        "target_name": TARGET_NAME, "generated_at_utc": generated_at,
        "ledger_cutoff_metadata": {"rule": "as_of_utc < scheduled_first_pitch_utc; pregame retrieval and availability <= as_of_utc", "partition_metadata": {"kind": "chronological_metadata_only"}},
        "row_count": len(rows), "rows": rows, "exclusion_count": len(exclusions), "exclusions": exclusions,
        "provenance_references": {"schedule": schedule_provenance},
        "no_probability_status": "NO_PROBABILITY_CALCULATED", "model_publication_status": "MODEL_NOT_IMPLEMENTED",
    }


def _timing_reason(snapshot: Mapping[str, Any], scheduled: str | None) -> str | None:
    as_of = _utc(snapshot.get("as_of_utc")); first_pitch = _utc(scheduled)
    if as_of is None: return "INVALID_AS_OF_UTC"
    if first_pitch is None or as_of >= first_pitch: return "PREDICTION_CUTOFF_NOT_STRICTLY_PREGAME"
    provenance = snapshot.get("provenance")
    if not isinstance(provenance, Mapping): return "MALFORMED_SOURCE_EVIDENCE"
    retrieved = _utc(provenance.get("retrieved_at_utc")); available = _utc(snapshot.get("available_at_utc"))
    if retrieved is None or available is None: return "MALFORMED_SOURCE_EVIDENCE"
    if retrieved >= first_pitch or available >= first_pitch: return "PREGAME_SNAPSHOT_NOT_STRICTLY_PREGAME"
    if retrieved > as_of: return "PREGAME_SNAPSHOT_RETRIEVED_AFTER_AS_OF_UTC"
    if available > as_of: return "PREGAME_SNAPSHOT_AVAILABLE_AFTER_AS_OF_UTC"
    return None


def _final_labels(payload: Mapping[str, Any]) -> dict[int, int] | None:
    teams = _mapping(payload.get("teams")); result: dict[int, int] = {}
    for side in ("away", "home"):
        team = _mapping(teams.get(side)); players = _mapping(team.get("players")); batters = team.get("batters")
        ids = batters if isinstance(batters, list) else list(players)
        for key in ids:
            record = _mapping(players.get(f"ID{key}") or players.get(str(key)))
            player_id = _positive(_mapping(record.get("person")).get("id")); hits = _mapping(_mapping(record.get("stats")).get("batting")).get("hits")
            if not player_id or not isinstance(hits, int) or isinstance(hits, bool) or hits < 0: return None
            result[player_id] = hits
    return result


def _payload_game_pk(payload: Mapping[str, Any]) -> int | None:
    return _positive(payload.get("gamePk")) or _positive(_mapping(_mapping(payload.get("gameData")).get("game")).get("pk"))


def _lineup_id_mismatch(payload: Mapping[str, Any], side: str) -> bool:
    team = _mapping(_mapping(_mapping(payload.get("liveData")).get("boxscore")).get("teams")).get(side)
    team = _mapping(team); players = _mapping(team.get("players")); order = team.get("battingOrder")
    if not isinstance(order, list): return False
    return any(
        _positive(entry) is not None
        and _positive(_mapping(_mapping(players.get(f"ID{entry}") or players.get(str(entry))).get("person")).get("id")) != _positive(entry)
        for entry in order
    )


def _pregame_starters(snapshot: Mapping[str, Any], context: GameContext) -> tuple[dict[str, int | None], list[dict[str, Any]]]:
    evidence = snapshot.get("starter_evidence"); selected: dict[str, int | None] = {}; exclusions: list[dict[str, Any]] = []
    for side in ("away", "home"):
        records = _mapping(evidence).get(side) if isinstance(evidence, Mapping) else None
        records = records if isinstance(records, list) else []
        probable = None
        for record in records:
            if not isinstance(record, Mapping): continue
            player_id = _positive(record.get("player_mlbam_id")); kind = record.get("kind"); available = _utc(record.get("available_at_utc"))
            if kind == "actual" and (available is None or available > _utc(snapshot.get("as_of_utc")) or available >= _utc(context.scheduled_start_utc)):
                exclusions.append(_exclusion(context.game_pk, player_id, side, "UNVERIFIED", "POSTGAME_ACTUAL_STARTER_BACKFILL"))
            elif kind == "actual" and player_id:
                selected[side] = player_id
            elif kind == "probable" and player_id and probable is None:
                probable = player_id
        selected.setdefault(side, probable)
    return selected, exclusions


def _health_reason(provenance: Any) -> str | None:
    if not isinstance(provenance, Mapping): return "MALFORMED_SOURCE_EVIDENCE"
    freshness = provenance.get("source_freshness_status"); failure = provenance.get("source_failure_status")
    if freshness == "STALE": return "STALE_SOURCE_EVIDENCE"
    if failure == "RETRYABLE": return "RETRYABLE_SOURCE_EVIDENCE"
    if failure == "TERMINAL": return "TERMINAL_SOURCE_EVIDENCE"
    if freshness != "PASS" or failure != "PASS" or not _is_provenance(provenance): return "MALFORMED_SOURCE_EVIDENCE"
    return None


def _health_status(provenance: Any) -> str:
    return "PASS" if _health_reason(provenance) is None else "UNVERIFIED"


def _is_provenance(value: Mapping[str, Any]) -> bool:
    return all(_text(value.get(key)) for key in ("source", "endpoint_or_url", "retrieved_at_utc", "raw_response_hash")) and _is_hash(value.get("raw_response_hash"))


def _exclusion(game_pk: int | None, player_id: int | None, side: str | None, status: str, reason: str) -> dict[str, Any]:
    return {"game_pk": game_pk, "player_mlbam_id": player_id, "team_side": side, "status": status if status in HEALTH else "UNVERIFIED", "reason": reason}


def _status_for_reason(reason: str) -> str:
    if reason.startswith("STALE"): return "STALE"
    if reason.startswith("RETRYABLE"): return "RETRYABLE"
    if reason.startswith("TERMINAL"): return "TERMINAL"
    return "UNVERIFIED"


def _mapping(value: Any) -> Mapping[str, Any]: return value if isinstance(value, Mapping) else {}
def _positive(value: Any) -> int | None: return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None
def _text(value: Any) -> str | None: return value if isinstance(value, str) and value else None
def _is_hash(value: Any) -> bool: return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)
def _utc(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.endswith(("Z", "+00:00")): return None
    try: parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError: return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo and parsed.utcoffset() == timezone.utc.utcoffset(parsed) else None
