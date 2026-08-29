from __future__ import annotations

from dataclasses import dataclass
from typing import Any

PROHIBITED_TERMS = (
    "hit streak",
    "game hit streak",
    "last 7",
    "last seven",
    "last night",
    "due",
    "drought",
    "games since",
    "pa since",
)

ALLOWED_PROP_FAMILIES = {
    "hit",
    "total_bases",
    "home_run",
    "runs",
    "rbi",
    "h_r",
}


@dataclass(frozen=True)
class QueueDecision:
    valid: bool
    disposition: str
    reasons: tuple[str, ...]


def validate_discovery_record(record: dict[str, Any]) -> QueueDecision:
    reasons: list[str] = []
    claim = str(record.get("raw_claim", "")).lower()
    source_type = record.get("source_type")
    sample_n = float(record.get("sample_n", 0) or 0)
    stabilization = record.get("stabilization_status")
    lineup_status = record.get("lineup_status")
    prop_family = record.get("prop_family")
    feature_mapping = record.get("feature_mapping") or []

    required = (
        "analysis_date",
        "game_pk",
        "player_mlbam_id",
        "candidate_name",
        "source_url",
        "retrieved_at_utc",
        "sample_type",
        "sample_n",
        "stabilization_status",
        "feature_mapping",
    )
    missing = [field for field in required if not record.get(field)]
    if missing:
        reasons.append(f"missing required fields: {', '.join(missing)}")

    if prop_family not in ALLOWED_PROP_FAMILIES:
        reasons.append("invalid prop family")

    if any(term in claim for term in PROHIBITED_TERMS):
        reasons.append("prohibited recency, streak, or due-logic evidence")

    if source_type == "bvp_display" and sample_n < 50:
        reasons.append("BvP under 50 PA: descriptive only, not predictive")

    if not feature_mapping:
        reasons.append("no model feature mapping")

    if stabilization in {"fail", "unverified"}:
        reasons.append("stabilization requirement not satisfied")

    if lineup_status != "confirmed":
        reasons.append("lineup is not confirmed")

    if reasons:
        return QueueDecision(False, "drop", tuple(reasons))

    return QueueDecision(True, "advance", ("validated for candidate filter table",))


def market_ready(record: dict[str, Any]) -> QueueDecision:
    required = ("market_book", "market_line", "market_price")
    missing = [field for field in required if record.get(field) in (None, "")]
    if missing:
        return QueueDecision(False, "hold", (f"missing market fields: {', '.join(missing)}",))

    if record.get("disposition") != "advance":
        return QueueDecision(False, "hold", ("candidate has not passed queue validation",))

    return QueueDecision(True, "priced", ("eligible for fair-probability and devig workflow",))
