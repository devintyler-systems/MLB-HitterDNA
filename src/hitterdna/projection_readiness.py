"""Pure, fail-closed readiness evaluation for the future Hit Probability v0."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Literal, Mapping


ProjectionTarget = Literal["hit_probability_v0"]
ReadinessStatus = Literal["READY", "UNVERIFIED", "STALE", "RETRYABLE", "TERMINAL", "DEFERRED"]

HIT_PROBABILITY_V0 = "hit_probability_v0"
TARGET_DEFINITION = "P(at least one official hit in a game | information available before first pitch)"
FEATURE_FAMILIES = (
    "historical_labels", "lineup_pa", "hitter_baseline", "starter_handedness",
    "regressed_platoon", "arsenal_fit", "park", "bearing_relative_weather",
)
REQUIRED_ENDPOINTS = {
    "historical_labels": "mlb_statsapi_box_scores",
    "lineup_pa": "mlb_statsapi_lineup_confirmation",
    "hitter_baseline": "savant_expected_statistics",
    "starter_handedness": "mlb_statsapi_probable_pitchers",
    "regressed_platoon": "savant_custom_leaderboards",
    "arsenal_fit": "savant_pitch_arsenal_batter",
    "park": "savant_statcast_park_factors",
    "bearing_relative_weather": "deferred_weather_interface",
}
REQUIRED_KEYS = {
    "historical_labels": ("game_pk", "player_mlbam_id", "game_date", "season"),
    "lineup_pa": ("game_pk", "player_mlbam_id", "game_date"),
    "hitter_baseline": ("player_mlbam_id", "season"),
    "starter_handedness": ("game_pk", "opponent_pitcher_mlbam_id", "game_date"),
    "regressed_platoon": ("player_mlbam_id", "season"),
    "arsenal_fit": ("player_mlbam_id", "season"),
    "park": ("venue_mlbam_id", "season"),
    "bearing_relative_weather": ("game_pk", "venue_mlbam_id", "game_date"),
}


@dataclass(frozen=True)
class SourceProvenanceReference:
    source: str | None = None
    endpoint_or_url: str | None = None
    retrieved_at_utc: str | None = None
    raw_response_hash: str | None = None
    source_freshness_status: str | None = None
    source_failure_status: str | None = None


@dataclass(frozen=True)
class PairedPregameSnapshotEvidence:
    """Leakage-control evidence paired to one historical outcome label."""

    source_reference_or_url: str | None = None
    raw_response_hash: str | None = None
    retrieved_at_utc: str | None = None
    available_at_utc: str | None = None
    canonical_keys: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class FeatureEvidence:
    """One local feature-family evidence summary; it performs no retrieval."""

    endpoint_id: str | None = None
    canonical_keys: Mapping[str, Any] | None = None
    provenance: SourceProvenanceReference | None = None
    status: ReadinessStatus = "UNVERIFIED"
    reason: str | None = None
    enabled: bool = True
    venue_name: str | None = None
    season: int | None = None
    fallback: str | None = None
    available_at_utc: str | None = None
    paired_pregame_snapshot: PairedPregameSnapshotEvidence | None = None


@dataclass(frozen=True)
class ProjectionReadinessRequest:
    target: ProjectionTarget
    as_of_utc: str
    scheduled_first_pitch_utc: str
    features: Mapping[str, FeatureEvidence]


@dataclass(frozen=True)
class ReadinessBlocker:
    feature_family: str
    required_source_endpoint: str
    canonical_keys: tuple[str, ...]
    reason: str
    prevents_training: bool
    prevents_pregame_inference: bool


@dataclass(frozen=True)
class FeatureReadiness:
    feature_family: str
    required_source_endpoint: str
    canonical_keys: tuple[str, ...]
    status: ReadinessStatus
    reason: str | None
    provenance_reference: SourceProvenanceReference | None
    available_at_utc: str | None
    paired_pregame_snapshot: PairedPregameSnapshotEvidence | None


@dataclass(frozen=True)
class ProjectionReadinessArtifact:
    target: ProjectionTarget
    target_definition: str
    as_of_utc: str
    scheduled_first_pitch_utc: str
    no_probability_status: Literal["NO_PROBABILITY_CALCULATED"]
    model_publication_status: Literal["MODEL_NOT_IMPLEMENTED"]
    training_status: ReadinessStatus
    pregame_inference_status: ReadinessStatus
    feature_readiness: tuple[FeatureReadiness, ...]
    blockers: tuple[ReadinessBlocker, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "target_definition": self.target_definition,
            "as_of_utc": self.as_of_utc,
            "scheduled_first_pitch_utc": self.scheduled_first_pitch_utc,
            "no_probability_status": self.no_probability_status,
            "model_publication_status": self.model_publication_status,
            "training_status": self.training_status,
            "pregame_inference_status": self.pregame_inference_status,
            "feature_readiness": [
                {
                    "feature_family": item.feature_family,
                    "required_source_endpoint": item.required_source_endpoint,
                    "canonical_keys": list(item.canonical_keys),
                    "status": item.status,
                    "reason": item.reason,
                    "provenance_reference": asdict(item.provenance_reference) if item.provenance_reference else None,
                    "available_at_utc": item.available_at_utc,
                    "paired_pregame_snapshot": asdict(item.paired_pregame_snapshot) if item.paired_pregame_snapshot else None,
                }
                for item in self.feature_readiness
            ],
            "blockers": [
                {
                    "feature_family": item.feature_family,
                    "required_source_endpoint": item.required_source_endpoint,
                    "canonical_keys": list(item.canonical_keys),
                    "reason": item.reason,
                    "prevents_training": item.prevents_training,
                    "prevents_pregame_inference": item.prevents_pregame_inference,
                }
                for item in self.blockers
            ],
        }


def evaluate_projection_readiness(request: ProjectionReadinessRequest) -> ProjectionReadinessArtifact:
    """Evaluate declared local evidence without producing a model or probability."""

    if request.target != HIT_PROBABILITY_V0:
        raise ValueError("unsupported projection target")
    as_of = _parse_utc_timestamp(request.as_of_utc)
    if as_of is None:
        raise ValueError("INVALID_AS_OF_UTC")
    scheduled_first_pitch = _parse_utc_timestamp(request.scheduled_first_pitch_utc)
    if scheduled_first_pitch is None:
        raise ValueError("INVALID_SCHEDULED_FIRST_PITCH_UTC")
    cutoff_blocker = _prediction_cutoff_blocker(as_of, scheduled_first_pitch)

    families: list[FeatureReadiness] = []
    blockers: list[ReadinessBlocker] = []
    for family in FEATURE_FAMILIES:
        evidence = request.features.get(family)
        readiness = _evaluate_family(family, evidence, as_of, scheduled_first_pitch)
        families.append(readiness)
        if readiness.status != "READY":
            prevents_training, prevents_inference = _blocker_flags(family, evidence)
            blockers.append(ReadinessBlocker(
                family, readiness.required_source_endpoint, readiness.canonical_keys,
                readiness.reason or "feature is not ready", prevents_training, prevents_inference,
            ))

    if cutoff_blocker is not None:
        blockers.append(cutoff_blocker)
    return ProjectionReadinessArtifact(
        target=request.target,
        target_definition=TARGET_DEFINITION,
        as_of_utc=request.as_of_utc,
        scheduled_first_pitch_utc=request.scheduled_first_pitch_utc,
        no_probability_status="NO_PROBABILITY_CALCULATED",
        model_publication_status="MODEL_NOT_IMPLEMENTED",
        training_status="UNVERIFIED" if cutoff_blocker else _aggregate_status(families, blockers, "training"),
        pregame_inference_status="UNVERIFIED" if cutoff_blocker else _aggregate_status(families, blockers, "pregame_inference"),
        feature_readiness=tuple(families),
        blockers=tuple(blockers),
    )


def _evaluate_family(
    family: str, evidence: FeatureEvidence | None, as_of: datetime, scheduled_first_pitch: datetime,
) -> FeatureReadiness:
    endpoint = REQUIRED_ENDPOINTS[family]
    keys = REQUIRED_KEYS[family]
    if evidence is None:
        return _unverified(family, endpoint, keys, "MISSING_FEATURE_EVIDENCE")
    if family in {"park", "bearing_relative_weather"} and not evidence.enabled:
        return FeatureReadiness(
            family, endpoint, keys, "DEFERRED", "ADJUSTMENT_NOT_ENABLED", evidence.provenance,
            evidence.available_at_utc, evidence.paired_pregame_snapshot,
        )
    if evidence.endpoint_id != endpoint:
        return _unverified(family, endpoint, keys, "UNAUTHORIZED_OR_MISSING_SOURCE_ENDPOINT", evidence)
    if family == "arsenal_fit" and evidence.fallback == "bvp":
        return _unverified(family, endpoint, keys, "ARSENAL_FIT_NO_BVP_FALLBACK", evidence)
    limitation = _park_limitation(family, evidence)
    if limitation:
        return _unverified(family, endpoint, keys, limitation, evidence)
    if evidence.status != "READY":
        status: ReadinessStatus = evidence.status if evidence.status in {
            "UNVERIFIED", "STALE", "RETRYABLE", "TERMINAL", "DEFERRED"
        } else "UNVERIFIED"
        return FeatureReadiness(
            family, endpoint, keys, status, evidence.reason or f"SOURCE_{status}", evidence.provenance,
            evidence.available_at_utc, evidence.paired_pregame_snapshot,
        )
    missing_keys = [key for key in keys if not _canonical_key_present(evidence.canonical_keys, key)]
    if missing_keys:
        return _unverified(family, endpoint, keys, f"MISSING_CANONICAL_KEYS:{','.join(missing_keys)}", evidence)
    if family == "historical_labels":
        label_problem = _label_provenance_problem(evidence.provenance)
        if label_problem:
            return _unverified(family, endpoint, keys, label_problem, evidence)
        snapshot_problem = _paired_pregame_snapshot_problem(
            evidence.paired_pregame_snapshot, evidence.canonical_keys, as_of, scheduled_first_pitch,
        )
        if snapshot_problem:
            return _unverified(family, endpoint, keys, snapshot_problem, evidence)
        return FeatureReadiness(
            family, endpoint, keys, "READY", None, evidence.provenance,
            evidence.available_at_utc, evidence.paired_pregame_snapshot,
        )
    problem = _provenance_problem(evidence.provenance, as_of)
    if problem:
        return _unverified(family, endpoint, keys, problem, evidence)
    availability_problem = _availability_problem(evidence.available_at_utc, as_of)
    if availability_problem:
        return _unverified(family, endpoint, keys, availability_problem, evidence)
    return FeatureReadiness(
        family, endpoint, keys, "READY", None, evidence.provenance,
        evidence.available_at_utc, evidence.paired_pregame_snapshot,
    )


def _park_limitation(family: str, evidence: FeatureEvidence) -> str | None:
    if family != "park":
        return None
    venue = evidence.venue_name.strip().casefold() if isinstance(evidence.venue_name, str) else ""
    if venue == "sutter health park":
        return "SUTTER_HEALTH_PARK_FACTOR_UNVERIFIED"
    if venue == "tropicana field" and isinstance(evidence.season, int) and evidence.season < 2026:
        return "TROPICANA_PRE_2026_PARK_FACTOR_DISCONTINUITY"
    return None


def _provenance_problem(
    provenance: SourceProvenanceReference | None, as_of: datetime,
) -> str | None:
    problem = _base_provenance_problem(provenance)
    if problem:
        return problem
    assert provenance is not None
    retrieved_at = _parse_utc_timestamp(provenance.retrieved_at_utc)
    if retrieved_at is None:
        return "INVALID_FEATURE_RETRIEVED_AT_UTC"
    if retrieved_at > as_of:
        return "FEATURE_RETRIEVED_AFTER_AS_OF_UTC"
    return None


def _label_provenance_problem(provenance: SourceProvenanceReference | None) -> str | None:
    """Validate final outcome provenance without applying a pregame cutoff."""

    problem = _base_provenance_problem(provenance)
    if problem:
        return problem
    assert provenance is not None
    return "INVALID_LABEL_PROVENANCE" if _parse_utc_timestamp(provenance.retrieved_at_utc) is None else None


def _paired_pregame_snapshot_problem(
    snapshot: PairedPregameSnapshotEvidence | None,
    label_keys: Mapping[str, Any] | None,
    as_of: datetime,
    scheduled_first_pitch: datetime,
) -> str | None:
    if snapshot is None:
        return "MISSING_PAIRED_PREGAME_SNAPSHOT"
    if (
        not _nonempty(snapshot.source_reference_or_url)
        or not _is_sha256(snapshot.raw_response_hash)
        or not _paired_snapshot_keys_match(snapshot.canonical_keys, label_keys)
    ):
        return "INVALID_PAIRED_PREGAME_SNAPSHOT_PROVENANCE"
    retrieved_at = _parse_utc_timestamp(snapshot.retrieved_at_utc)
    available_at = _parse_utc_timestamp(snapshot.available_at_utc)
    if retrieved_at is None or available_at is None:
        return "INVALID_PAIRED_PREGAME_SNAPSHOT_PROVENANCE"
    if retrieved_at >= scheduled_first_pitch or available_at >= scheduled_first_pitch:
        return "PAIRED_PREGAME_SNAPSHOT_NOT_STRICTLY_PREGAME"
    if retrieved_at > as_of:
        return "PAIRED_PREGAME_SNAPSHOT_RETRIEVED_AFTER_AS_OF_UTC"
    if available_at > as_of:
        return "PAIRED_PREGAME_SNAPSHOT_AVAILABLE_AFTER_AS_OF_UTC"
    return None


def _availability_problem(available_at_utc: str | None, as_of: datetime) -> str | None:
    available_at = _parse_utc_timestamp(available_at_utc)
    if available_at is None:
        return "MISSING_OR_INVALID_FEATURE_AVAILABILITY_AT_UTC"
    if available_at > as_of:
        return "FEATURE_NOT_AVAILABLE_AT_AS_OF_UTC"
    return None


def _prediction_cutoff_blocker(
    as_of: datetime, scheduled_first_pitch: datetime,
) -> ReadinessBlocker | None:
    if as_of < scheduled_first_pitch:
        return None
    return ReadinessBlocker(
        "prediction_cutoff", "mlb_statsapi_schedule_by_date",
        ("game_pk", "scheduled_first_pitch_utc"),
        "PREDICTION_CUTOFF_NOT_STRICTLY_PREGAME", True, True,
    )


def _blocker_flags(family: str, evidence: FeatureEvidence | None) -> tuple[bool, bool]:
    if family == "historical_labels":
        return True, False
    if family in {"park", "bearing_relative_weather"} and evidence is not None and not evidence.enabled:
        return False, False
    return True, True


def _aggregate_status(
    features: list[FeatureReadiness], blockers: list[ReadinessBlocker], scope: str,
) -> ReadinessStatus:
    relevant = [b for b in blockers if (b.prevents_training if scope == "training" else b.prevents_pregame_inference)]
    if not relevant:
        return "READY"
    statuses = {item.status for item in features if any(b.feature_family == item.feature_family for b in relevant)}
    for status in ("TERMINAL", "RETRYABLE", "STALE", "UNVERIFIED", "DEFERRED"):
        if status in statuses:
            return status
    return "UNVERIFIED"


def _unverified(
    family: str, endpoint: str, keys: tuple[str, ...], reason: str,
    evidence: FeatureEvidence | None = None,
) -> FeatureReadiness:
    return FeatureReadiness(
        family, endpoint, keys, "UNVERIFIED", reason,
        evidence.provenance if evidence else None,
        evidence.available_at_utc if evidence else None,
        evidence.paired_pregame_snapshot if evidence else None,
    )


def _canonical_key_present(keys: Mapping[str, Any] | None, key: str) -> bool:
    if not isinstance(keys, Mapping):
        return False
    value = keys.get(key)
    if key in {"game_pk", "player_mlbam_id", "opponent_pitcher_mlbam_id", "venue_mlbam_id"}:
        return isinstance(value, int) and not isinstance(value, bool) and value > 0
    return _nonempty(value)


def _nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _base_provenance_problem(provenance: SourceProvenanceReference | None) -> str | None:
    if provenance is None:
        return "MISSING_PROVENANCE_REFERENCE"
    values = (
        provenance.source, provenance.endpoint_or_url, provenance.retrieved_at_utc,
        provenance.raw_response_hash, provenance.source_freshness_status, provenance.source_failure_status,
    )
    if not all(_nonempty(value) for value in values):
        return "INCOMPLETE_PROVENANCE_REFERENCE"
    if not _is_sha256(provenance.raw_response_hash):
        return "INVALID_RAW_RESPONSE_HASH"
    if provenance.source_freshness_status != "PASS":
        return "SOURCE_FRESHNESS_NOT_PASS"
    if provenance.source_failure_status != "PASS":
        return "SOURCE_FAILURE_NOT_PASS"
    return None


def _paired_snapshot_keys_match(
    snapshot_keys: Mapping[str, Any] | None, label_keys: Mapping[str, Any] | None,
) -> bool:
    return all(
        _canonical_key_present(snapshot_keys, key)
        and isinstance(label_keys, Mapping)
        and snapshot_keys[key] == label_keys.get(key)
        for key in ("game_pk", "player_mlbam_id")
    )


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _parse_utc_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value or not value.endswith(("Z", "+00:00")):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        return None
    return parsed.astimezone(timezone.utc)
