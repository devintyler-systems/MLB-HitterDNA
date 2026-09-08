"""Local immutable snapshot-manifest builder and validator; no network I/O."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from hitterdna.lineups import normalize_game_lineups
from hitterdna.statsapi import GameContext, normalize_schedule_contexts


ROLES = ("schedule", "pregame_live_feed", "final_box_score")
ENDPOINTS = {"schedule": "mlb_statsapi_schedule_by_date", "pregame_live_feed": "mlb_statsapi_lineup_confirmation", "final_box_score": "mlb_statsapi_box_scores"}
HEALTH = {"PASS", "STALE", "UNVERIFIED", "RETRYABLE", "TERMINAL"}
ROLE_ORDER = {role: index for index, role in enumerate(ROLES)}


def build_historical_snapshot_backfill_manifest(index_path: str | Path, snapshot_root: str | Path) -> dict[str, Any]:
    """Build a manifest from explicit paths, hashing bytes actually read."""

    index_file = Path(index_path).resolve(); root = Path(snapshot_root).resolve()
    index = _read_json(index_file)
    if not isinstance(index, Mapping) or not isinstance(index.get("games"), list):
        raise ValueError("MALFORMED_INDEX")
    generated = _utc(index.get("generated_at_utc")); interval = index.get("date_interval")
    if generated is None or not isinstance(interval, Mapping) or _date(interval.get("start")) is None or _date(interval.get("end")) is None:
        raise ValueError("MALFORMED_INDEX")
    games: list[dict[str, Any]] = []; exclusions: list[dict[str, Any]] = []; provenance: list[dict[str, Any]] = []
    health_counts: Counter[str] = Counter(); complete = valid_pregame = ledger_eligible = 0; duplicate_conflicts = 0
    seen_games: set[int] = set()
    for offered in index["games"]:
        if not isinstance(offered, Mapping):
            exclusions.append(_exclusion(None, None, "MALFORMED_INDEX_ENTRY")); continue
        game_pk = _positive(offered.get("game_pk")); as_of = _utc(offered.get("as_of_utc")); first = _utc(offered.get("scheduled_first_pitch_utc"))
        if game_pk is None:
            exclusions.append(_exclusion(None, None, "INVALID_GAME_PK")); continue
        if game_pk in seen_games:
            exclusions.append(_exclusion(game_pk, None, "DUPLICATE_GAME_ENTRY")); continue
        seen_games.add(game_pk)
        if not _text(offered.get("game_date")):
            exclusions.append(_exclusion(game_pk, None, "INVALID_GAME_DATE")); continue
        if as_of is None or first is None:
            exclusions.append(_exclusion(game_pk, None, "INVALID_TIMESTAMP")); continue
        if as_of >= first:
            exclusions.append(_exclusion(game_pk, None, "PREDICTION_CUTOFF_NOT_STRICTLY_PREGAME")); continue
        role_items = offered.get("snapshots")
        if not isinstance(role_items, list):
            exclusions.append(_exclusion(game_pk, None, "MISSING_SNAPSHOT_ROLE")); continue
        by_role: dict[str, Mapping[str, Any]] = {}
        conflict = False
        for item in role_items:
            role = item.get("role") if isinstance(item, Mapping) else None
            if role not in ROLES:
                exclusions.append(_exclusion(game_pk, role, "INVALID_SNAPSHOT_ROLE")); conflict = True; continue
            if role in by_role:
                duplicate_conflicts += 1
                exclusions.append(_exclusion(game_pk, role, "DUPLICATE_CONFLICTING_ROLE_SNAPSHOT")); conflict = True; continue
            by_role[role] = item
        missing = [role for role in ROLES if role not in by_role]
        if missing:
            exclusions.append(_exclusion(game_pk, missing[0], "MISSING_SNAPSHOT_ROLE")); continue
        records: dict[str, dict[str, Any]] = {}; valid = True
        for role in ROLES:
            record, reason = _validate_snapshot(by_role[role], role, game_pk, as_of, first, root, str(offered["game_date"]))
            if reason:
                exclusions.append(_exclusion(game_pk, role, reason, _status_for_reason(reason))); valid = False; continue
            assert record is not None
            records[role] = record; health_counts[record["source_freshness_status"] + "/" + record["source_failure_status"]] += 1
        if not valid or conflict:
            continue
        schedule_payload = _load_snapshot_payload(records["schedule"], root)
        pregame_payload = _load_snapshot_payload(records["pregame_live_feed"], root)
        context = _schedule_context(schedule_payload, game_pk, str(offered["game_date"]), first)
        if context is None or not isinstance(pregame_payload, Mapping):
            exclusions.append(_exclusion(game_pk, "pregame_live_feed", "MALFORMED_SOURCE_EVIDENCE")); continue
        lineups = normalize_game_lineups(context, dict(pregame_payload), records["pregame_live_feed"]["retrieved_at_utc"])
        away_count = len(lineups.away_lineup.players) if lineups.away_lineup.lineup_status == "confirmed" else 0
        home_count = len(lineups.home_lineup.players) if lineups.home_lineup.lineup_status == "confirmed" else 0
        records["pregame_live_feed"].update(
            away_lineup_status=lineups.away_lineup.lineup_status,
            home_lineup_status=lineups.home_lineup.lineup_status,
            away_confirmed_hitter_count=away_count,
            home_confirmed_hitter_count=home_count,
        )
        if away_count == 0 and home_count == 0:
            exclusions.append(_exclusion(game_pk, "pregame_live_feed", "PARTIAL_OR_MISSING_OFFICIAL_LINEUP")); continue
        provenance.extend(records.values())
        valid_pregame += 1
        complete += 1
        games.append({"game_pk": game_pk, "game_date": str(offered["game_date"]), "scheduled_first_pitch_utc": offered["scheduled_first_pitch_utc"], "as_of_utc": offered["as_of_utc"], "snapshots": [records[role] for role in ROLES]})
        ledger_eligible += 1
    games.sort(key=lambda item: item["game_pk"]); exclusions.sort(key=lambda item: (item["game_pk"] or 0, item["snapshot_role"] or "", item["reason"]))
    report = {"games_offered": len(index["games"]), "complete_triplets": complete, "valid_pregame_snapshots": valid_pregame, "valid_ledger_eligible_games": ledger_eligible, "excluded_games_by_reason": dict(sorted(Counter(item["reason"] for item in exclusions).items())), "duplicate_snapshot_conflicts": duplicate_conflicts, "source_health_distribution": dict(sorted(health_counts.items()))}
    return {"artifact_type": "historical_snapshot_backfill_manifest", "corpus_version": str(index.get("corpus_version", "historical-snapshot-backfill-v0")), "generated_at_utc": index["generated_at_utc"], "date_interval": dict(interval), "snapshot_root_reference": str(snapshot_root), "games": games, "coverage_summary": report, "exclusions": exclusions, "provenance_references": provenance}


def validate_historical_snapshot_backfill_manifest(manifest: Mapping[str, Any]) -> None:
    """Perform strict local structural validation of a built manifest."""

    required = {"artifact_type", "corpus_version", "generated_at_utc", "date_interval", "snapshot_root_reference", "games", "coverage_summary", "exclusions", "provenance_references"}
    if not isinstance(manifest, Mapping) or set(manifest) != required or manifest.get("artifact_type") != "historical_snapshot_backfill_manifest": raise ValueError("MALFORMED_MANIFEST")
    if _utc(manifest.get("generated_at_utc")) is None or not _text(manifest.get("corpus_version")) or not _text(manifest.get("snapshot_root_reference")): raise ValueError("MALFORMED_MANIFEST")
    interval = manifest.get("date_interval")
    if not isinstance(interval, Mapping) or set(interval) != {"start", "end"} or _date(interval.get("start")) is None or _date(interval.get("end")) is None: raise ValueError("MALFORMED_MANIFEST")
    if not isinstance(manifest.get("games"), list) or not isinstance(manifest.get("exclusions"), list): raise ValueError("MALFORMED_MANIFEST")
    if not isinstance(manifest.get("coverage_summary"), Mapping) or not isinstance(manifest.get("provenance_references"), list):
        raise ValueError("MALFORMED_MANIFEST")
    for game in manifest["games"]:
        if not isinstance(game, Mapping) or not _positive(game.get("game_pk")) or not isinstance(game.get("snapshots"), list) or [item.get("role") for item in game["snapshots"]] != list(ROLES): raise ValueError("MALFORMED_MANIFEST")
        if _utc(game.get("scheduled_first_pitch_utc")) is None or _utc(game.get("as_of_utc")) is None or _utc(game.get("as_of_utc")) >= _utc(game.get("scheduled_first_pitch_utc")): raise ValueError("PREDICTION_CUTOFF_NOT_STRICTLY_PREGAME")
        for snapshot in game["snapshots"]:
            if not isinstance(snapshot, Mapping) or not _positive(snapshot.get("game_pk")) or snapshot.get("game_pk") != game["game_pk"]: raise ValueError("MISMATCHED_GAME_PK")
            if set(snapshot) - {"endpoint_id", "source", "endpoint_or_url", "submitted_parameters", "retrieved_at_utc", "raw_response_hash", "source_freshness_status", "source_failure_status", "canonical_keys", "snapshot_path", "role", "game_pk", "available_at_utc", "outcome_only", "away_lineup_status", "home_lineup_status", "away_confirmed_hitter_count", "home_confirmed_hitter_count"}: raise ValueError("MALFORMED_MANIFEST")
            if snapshot.get("role") == "pregame_live_feed":
                fields = ("away_lineup_status", "home_lineup_status", "away_confirmed_hitter_count", "home_confirmed_hitter_count")
                if any(field not in snapshot for field in fields): raise ValueError("MALFORMED_MANIFEST")
                for side in ("away", "home"):
                    status = snapshot[f"{side}_lineup_status"]; count = snapshot[f"{side}_confirmed_hitter_count"]
                    if status not in {"confirmed", "unconfirmed"} or not isinstance(count, int) or isinstance(count, bool) or count < 0 or count > 9 or count != (9 if status == "confirmed" else 0): raise ValueError("MALFORMED_MANIFEST")
                if _utc(snapshot.get("available_at_utc")) is None: raise ValueError("MALFORMED_MANIFEST")
            if snapshot.get("role") == "final_box_score" and snapshot.get("outcome_only") is not True: raise ValueError("FINAL_BOX_NOT_OUTCOME_ONLY")
    for exclusion in manifest["exclusions"]:
        if not isinstance(exclusion, Mapping) or set(exclusion) != {"game_pk", "status", "reason", "snapshot_role"} or exclusion.get("status") not in {"UNVERIFIED", "STALE", "RETRYABLE", "TERMINAL"}: raise ValueError("MALFORMED_MANIFEST")


def _validate_snapshot(item: Mapping[str, Any], role: str, game_pk: int, as_of: datetime, first: datetime, root: Path, game_date: str) -> tuple[dict[str, Any] | None, str | None]:
    if item.get("endpoint_id") != ENDPOINTS[role] or item.get("role") != role or not _text(item.get("snapshot_path")) or Path(item["snapshot_path"]).is_absolute(): return None, "MALFORMED_SOURCE_EVIDENCE"
    relative = Path(item["snapshot_path"])
    if ".." in relative.parts: return None, "PATH_TRAVERSAL_OR_MISSING_SNAPSHOT"
    path = (root / relative).resolve()
    if root not in path.parents or not path.is_file(): return None, "PATH_TRAVERSAL_OR_MISSING_SNAPSHOT"
    try: raw = path.read_bytes(); payload = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError): return None, "MALFORMED_SOURCE_EVIDENCE"
    actual_hash = hashlib.sha256(raw).hexdigest()
    if item.get("raw_response_hash") != actual_hash: return None, "RAW_HASH_MISMATCH"
    if not _is_hash(actual_hash) or not _source_record_valid(item, role): return None, "MALFORMED_SOURCE_EVIDENCE"
    if item.get("source_freshness_status") == "STALE": return None, "STALE_SOURCE_EVIDENCE"
    if item.get("source_freshness_status") == "UNVERIFIED" or item.get("source_failure_status") == "UNVERIFIED": return None, "UNVERIFIED_SOURCE_EVIDENCE"
    if item.get("source_failure_status") == "RETRYABLE": return None, "RETRYABLE_SOURCE_EVIDENCE"
    if item.get("source_failure_status") == "TERMINAL": return None, "TERMINAL_SOURCE_EVIDENCE"
    if not isinstance(payload, Mapping): return None, "MALFORMED_SOURCE_EVIDENCE"
    if role != "schedule" and _payload_game_pk(payload) != game_pk: return None, "MISMATCHED_GAME_PK"
    if role == "final_box_score" and item.get("outcome_only") is not True: return None, "FINAL_BOX_NOT_OUTCOME_ONLY"
    if role == "schedule" and not _schedule_identity_matches(payload, game_pk, game_date, first): return None, "MALFORMED_SOURCE_EVIDENCE"
    if role == "final_box_score" and not isinstance(payload.get("teams"), Mapping): return None, "MALFORMED_SOURCE_EVIDENCE"
    retrieved = _utc(item.get("retrieved_at_utc"))
    if retrieved is None: return None, "INVALID_TIMESTAMP"
    if role == "pregame_live_feed":
        available = _utc(item.get("available_at_utc"))
        if available is None: return None, "INVALID_TIMESTAMP"
        if retrieved > as_of: return None, "PREGAME_SNAPSHOT_RETRIEVED_AFTER_AS_OF_UTC"
        if available > as_of: return None, "PREGAME_SNAPSHOT_AVAILABLE_AFTER_AS_OF_UTC"
        if retrieved >= first or available >= first: return None, "PREGAME_SNAPSHOT_NOT_STRICTLY_PREGAME"
    result = dict(item); result["raw_response_hash"] = actual_hash; result["canonical_keys"] = dict(item["canonical_keys"]); return result, None


def _source_record_valid(item: Mapping[str, Any], role: str) -> bool:
    required = ("source", "endpoint_or_url", "submitted_parameters", "retrieved_at_utc", "raw_response_hash", "canonical_keys", "source_freshness_status", "source_failure_status", "snapshot_path", "role", "endpoint_id")
    if not all(_text(item.get(key)) for key in ("source", "endpoint_or_url", "retrieved_at_utc", "snapshot_path", "role", "endpoint_id")) or not isinstance(item.get("submitted_parameters"), Mapping) or not isinstance(item.get("canonical_keys"), Mapping): return False
    if item.get("source_freshness_status") not in {"PASS", "STALE", "UNVERIFIED"} or item.get("source_failure_status") not in {"PASS", "UNVERIFIED", "RETRYABLE", "TERMINAL"}: return False
    if item.get("endpoint_id") != ENDPOINTS[role]: return False
    keys = item["canonical_keys"]; return _positive(keys.get("game_pk")) == _positive(item.get("game_pk")) and _positive(keys.get("game_pk")) is not None


def _schedule_identity_matches(payload: Any, game_pk: int, game_date: str, first: datetime) -> bool:
    return _schedule_context(payload, game_pk, game_date, first) is not None


def _schedule_context(payload: Any, game_pk: int, game_date: str, first: datetime) -> GameContext | None:
    if not isinstance(payload, Mapping): return None
    contexts = [context for context in normalize_schedule_contexts(payload) if context.game_pk == game_pk]
    if len(contexts) != 1: return None
    context = contexts[0]
    return context if context.analysis_date == game_date and _utc(context.scheduled_start_utc) == first else None


def _load_snapshot_payload(record: Mapping[str, Any], root: Path) -> Any:
    path = (root / record["snapshot_path"]).resolve()
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None


def _payload_game_pk(payload: Any) -> int | None:
    if not isinstance(payload, Mapping): return None
    return _positive(payload.get("gamePk")) or _positive(_mapping(_mapping(payload.get("gameData")).get("game")).get("pk")) or _schedule_game_pk(payload)


def _schedule_game_pk(payload: Mapping[str, Any]) -> int | None:
    for day in payload.get("dates", []) if isinstance(payload.get("dates"), list) else []:
        for game in day.get("games", []) if isinstance(day, Mapping) and isinstance(day.get("games"), list) else []:
            if isinstance(game, Mapping) and _positive(game.get("gamePk")): return _positive(game.get("gamePk"))
    return None


def _status_for_reason(reason: str) -> str:
    if reason == "STALE_SOURCE_EVIDENCE": return "STALE"
    if reason == "RETRYABLE_SOURCE_EVIDENCE": return "RETRYABLE"
    if reason == "TERMINAL_SOURCE_EVIDENCE": return "TERMINAL"
    return "UNVERIFIED"

def _exclusion(game_pk: int | None, role: Any, reason: str, status: str = "UNVERIFIED") -> dict[str, Any]: return {"game_pk": game_pk, "status": status, "reason": reason, "snapshot_role": role if role in ROLES else None}
def _read_json(path: Path) -> Any:
    try: return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error: raise ValueError("MALFORMED_INDEX") from error
def _mapping(value: Any) -> Mapping[str, Any]: return value if isinstance(value, Mapping) else {}
def _text(value: Any) -> bool: return isinstance(value, str) and bool(value.strip())
def _date(value: Any) -> str | None:
    if not isinstance(value, str): return None
    try:
        datetime.fromisoformat(value)
    except ValueError:
        return None
    return value if len(value) == 10 else None
def _positive(value: Any) -> int | None: return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None
def _is_hash(value: Any) -> bool: return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)
def _utc(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.endswith(("Z", "+00:00")): return None
    try: parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError: return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo and parsed.utcoffset() == timezone.utc.utcoffset(parsed) else None

