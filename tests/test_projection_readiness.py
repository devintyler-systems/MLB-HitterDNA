import ast
import json
from pathlib import Path
import socket

import jsonschema
import pytest

from hitterdna.projection_readiness import (
    FEATURE_FAMILIES,
    HIT_PROBABILITY_V0,
    FeatureEvidence,
    PairedPregameSnapshotEvidence,
    ProjectionReadinessRequest,
    SourceProvenanceReference,
    evaluate_projection_readiness,
)


ROOT = Path(__file__).parents[1]
HASH = "a" * 64


def _provenance(retrieved_at_utc: str = "2026-09-07T16:00:00Z") -> SourceProvenanceReference:
    return SourceProvenanceReference(
        source="captured fixture",
        endpoint_or_url="https://example.invalid/source",
        retrieved_at_utc=retrieved_at_utc,
        raw_response_hash=HASH,
        source_freshness_status="PASS",
        source_failure_status="PASS",
    )


def _keys(family: str) -> dict[str, int | str]:
    values: dict[str, int | str] = {
        "game_pk": 823175,
        "player_mlbam_id": 123456,
        "opponent_pitcher_mlbam_id": 654321,
        "venue_mlbam_id": 3312,
        "game_date": "2026-09-07",
        "season": "2026",
    }
    if family == "park":
        values["season"] = "2026"
    return values


def _paired_snapshot(
    *, retrieved_at_utc: str = "2026-09-07T16:00:00Z",
    available_at_utc: str = "2026-09-07T16:00:00Z",
) -> PairedPregameSnapshotEvidence:
    return PairedPregameSnapshotEvidence(
        source_reference_or_url="snapshots/2026-09-07/823175-pregame.json",
        raw_response_hash=HASH,
        retrieved_at_utc=retrieved_at_utc,
        available_at_utc=available_at_utc,
        canonical_keys={"game_pk": 823175, "player_mlbam_id": 123456},
    )


def _ready_features() -> dict[str, FeatureEvidence]:
    endpoints = {
        "historical_labels": "mlb_statsapi_box_scores",
        "lineup_pa": "mlb_statsapi_lineup_confirmation",
        "hitter_baseline": "savant_expected_statistics",
        "starter_handedness": "mlb_statsapi_probable_pitchers",
        "regressed_platoon": "savant_custom_leaderboards",
        "arsenal_fit": "savant_pitch_arsenal_batter",
        "park": "savant_statcast_park_factors",
        "bearing_relative_weather": "deferred_weather_interface",
    }
    features = {
        family: FeatureEvidence(
            endpoint_id=endpoints[family], canonical_keys=_keys(family), provenance=_provenance(),
            status="READY", enabled=True, available_at_utc="2026-09-07T16:00:00Z",
        )
        for family in FEATURE_FAMILIES
    }
    features["historical_labels"] = FeatureEvidence(
        endpoint_id="mlb_statsapi_box_scores", canonical_keys=_keys("historical_labels"),
        provenance=_provenance("2026-09-07T20:00:00Z"), status="READY",
        paired_pregame_snapshot=_paired_snapshot(),
    )
    return features


def _evaluate(
    features: dict[str, FeatureEvidence], *, as_of_utc: str = "2026-09-07T16:30:00Z",
    scheduled_first_pitch_utc: str = "2026-09-07T17:00:00Z",
):
    return evaluate_projection_readiness(ProjectionReadinessRequest(
        target=HIT_PROBABILITY_V0, as_of_utc=as_of_utc,
        scheduled_first_pitch_utc=scheduled_first_pitch_utc, features=features,
    ))


def _blocker(artifact, family: str):
    return next(blocker for blocker in artifact.blockers if blocker.feature_family == family)


def test_hit_probability_v0_cannot_be_ready_without_historical_labels() -> None:
    features = _ready_features()
    del features["historical_labels"]

    artifact = _evaluate(features)

    assert artifact.training_status == "UNVERIFIED"
    assert artifact.pregame_inference_status == "READY"
    blocker = _blocker(artifact, "historical_labels")
    assert blocker.required_source_endpoint == "mlb_statsapi_box_scores"
    assert blocker.prevents_training is True
    assert blocker.prevents_pregame_inference is False
    assert artifact.no_probability_status == "NO_PROBABILITY_CALCULATED"


def test_missing_starter_handedness_blocks_pregame_inference() -> None:
    features = _ready_features()
    features["starter_handedness"] = FeatureEvidence(
        endpoint_id="mlb_statsapi_probable_pitchers", canonical_keys=_keys("starter_handedness"),
        provenance=_provenance(), status="UNVERIFIED", reason="MISSING_STARTER_HANDEDNESS",
    )

    artifact = _evaluate(features)

    assert artifact.pregame_inference_status == "UNVERIFIED"
    assert _blocker(artifact, "starter_handedness").reason == "MISSING_STARTER_HANDEDNESS"


def test_missing_or_unverified_enabled_park_and_weather_fail_closed() -> None:
    features = _ready_features()
    features["park"] = FeatureEvidence(endpoint_id="savant_statcast_park_factors", enabled=True)
    features["bearing_relative_weather"] = FeatureEvidence(
        endpoint_id="deferred_weather_interface", canonical_keys=_keys("bearing_relative_weather"),
        provenance=_provenance(), status="UNVERIFIED", enabled=True, reason="WEATHER_NOT_VERIFIED",
    )

    artifact = _evaluate(features)

    assert artifact.pregame_inference_status == "UNVERIFIED"
    assert _blocker(artifact, "park").reason == "SOURCE_UNVERIFIED"
    assert _blocker(artifact, "bearing_relative_weather").reason == "WEATHER_NOT_VERIFIED"

    features["park"] = FeatureEvidence(
        endpoint_id="savant_statcast_park_factors", status="READY", enabled=True,
    )
    artifact = _evaluate(features)
    assert _blocker(artifact, "park").reason == "MISSING_CANONICAL_KEYS:venue_mlbam_id,season"


def test_park_limitations_are_machine_readable_without_fabricated_factor() -> None:
    features = _ready_features()
    features["park"] = FeatureEvidence(
        endpoint_id="savant_statcast_park_factors", canonical_keys=_keys("park"), provenance=_provenance(),
        status="READY", venue_name="Sutter Health Park", season=2026,
    )
    artifact = _evaluate(features)
    assert _blocker(artifact, "park").reason == "SUTTER_HEALTH_PARK_FACTOR_UNVERIFIED"

    features["park"] = FeatureEvidence(
        endpoint_id="savant_statcast_park_factors", canonical_keys=_keys("park"), provenance=_provenance(),
        status="READY", venue_name="Tropicana Field", season=2025,
    )
    artifact = _evaluate(features)
    assert _blocker(artifact, "park").reason == "TROPICANA_PRE_2026_PARK_FACTOR_DISCONTINUITY"


def test_missing_arsenal_data_never_falls_back_to_bvp() -> None:
    features = _ready_features()
    features["arsenal_fit"] = FeatureEvidence(
        endpoint_id="savant_pitch_arsenal_batter", canonical_keys=_keys("arsenal_fit"),
        provenance=_provenance(), status="UNVERIFIED", fallback="bvp",
    )

    artifact = _evaluate(features)

    assert _blocker(artifact, "arsenal_fit").reason == "ARSENAL_FIT_NO_BVP_FALLBACK"


@pytest.mark.parametrize(
    ("as_of_utc", "scheduled_first_pitch_utc", "reason"),
    [
        ("not-a-timestamp", "2026-09-07T17:00:00Z", "INVALID_AS_OF_UTC"),
        ("2026-09-07T16:30:00-07:00", "2026-09-07T17:00:00Z", "INVALID_AS_OF_UTC"),
        ("2026-09-07T16:30:00Z", "not-a-timestamp", "INVALID_SCHEDULED_FIRST_PITCH_UTC"),
        ("2026-09-07T16:30:00Z", "2026-09-07T17:00:00-07:00", "INVALID_SCHEDULED_FIRST_PITCH_UTC"),
    ],
)
def test_invalid_prediction_timestamps_fail_closed(
    as_of_utc: str, scheduled_first_pitch_utc: str, reason: str,
) -> None:
    with pytest.raises(ValueError, match=reason):
        _evaluate(
            _ready_features(), as_of_utc=as_of_utc,
            scheduled_first_pitch_utc=scheduled_first_pitch_utc,
        )


@pytest.mark.parametrize("scheduled_first_pitch_utc", ["2026-09-07T16:30:00Z", "2026-09-07T16:29:59Z"])
def test_prediction_cutoff_must_be_strictly_pregame(scheduled_first_pitch_utc: str) -> None:
    artifact = _evaluate(_ready_features(), scheduled_first_pitch_utc=scheduled_first_pitch_utc)

    cutoff = _blocker(artifact, "prediction_cutoff")
    assert cutoff.reason == "PREDICTION_CUTOFF_NOT_STRICTLY_PREGAME"
    assert cutoff.prevents_training is True
    assert cutoff.prevents_pregame_inference is True
    assert artifact.training_status == "UNVERIFIED"
    assert artifact.pregame_inference_status == "UNVERIFIED"


def test_ready_feature_retrieved_after_as_of_fails_closed() -> None:
    features = _ready_features()
    features["hitter_baseline"] = FeatureEvidence(
        endpoint_id="savant_expected_statistics", canonical_keys=_keys("hitter_baseline"),
        provenance=_provenance("2026-09-07T16:30:01Z"), status="READY",
        available_at_utc="2026-09-07T16:00:00Z",
    )

    artifact = _evaluate(features)

    assert _blocker(artifact, "hitter_baseline").reason == "FEATURE_RETRIEVED_AFTER_AS_OF_UTC"


def test_ready_feature_available_after_as_of_fails_closed() -> None:
    features = _ready_features()
    features["hitter_baseline"] = FeatureEvidence(
        endpoint_id="savant_expected_statistics", canonical_keys=_keys("hitter_baseline"),
        provenance=_provenance(), status="READY", available_at_utc="2026-09-07T16:30:01Z",
    )

    artifact = _evaluate(features)

    assert _blocker(artifact, "hitter_baseline").reason == "FEATURE_NOT_AVAILABLE_AT_AS_OF_UTC"


def test_ready_historical_labels_require_paired_pregame_snapshot() -> None:
    features = _ready_features()
    features["historical_labels"] = FeatureEvidence(
        endpoint_id="mlb_statsapi_box_scores", canonical_keys=_keys("historical_labels"),
        provenance=_provenance("2026-09-07T20:00:00Z"), status="READY",
    )

    artifact = _evaluate(features)

    blocker = _blocker(artifact, "historical_labels")
    assert blocker.reason == "MISSING_PAIRED_PREGAME_SNAPSHOT"
    assert blocker.prevents_training is True


def test_postgame_final_label_with_paired_pregame_snapshot_is_training_ready() -> None:
    artifact = _evaluate(_ready_features())

    label = next(item for item in artifact.feature_readiness if item.feature_family == "historical_labels")
    assert label.status == "READY"
    assert label.provenance_reference is not None
    assert label.provenance_reference.retrieved_at_utc == "2026-09-07T20:00:00Z"
    assert artifact.training_status == "READY"


def test_postgame_final_label_never_makes_pregame_inference_depend_on_postgame_data() -> None:
    features = _ready_features()
    features["historical_labels"] = FeatureEvidence(
        endpoint_id="mlb_statsapi_box_scores", canonical_keys=_keys("historical_labels"),
        provenance=_provenance("2026-09-08T01:00:00Z"), status="READY",
        paired_pregame_snapshot=_paired_snapshot(),
    )

    artifact = _evaluate(features)

    assert artifact.training_status == "READY"
    assert artifact.pregame_inference_status == "READY"


@pytest.mark.parametrize(
    ("snapshot", "reason"),
    [
        (
            PairedPregameSnapshotEvidence(
                source_reference_or_url=None, raw_response_hash=HASH,
                retrieved_at_utc="2026-09-07T16:00:00Z", available_at_utc="2026-09-07T16:00:00Z",
                canonical_keys={"game_pk": 823175, "player_mlbam_id": 123456},
            ),
            "INVALID_PAIRED_PREGAME_SNAPSHOT_PROVENANCE",
        ),
        (
            PairedPregameSnapshotEvidence(
                source_reference_or_url="snapshot", raw_response_hash="not-a-hash",
                retrieved_at_utc="2026-09-07T16:00:00Z", available_at_utc="2026-09-07T16:00:00Z",
                canonical_keys={"game_pk": 823175, "player_mlbam_id": 123456},
            ),
            "INVALID_PAIRED_PREGAME_SNAPSHOT_PROVENANCE",
        ),
        (
            PairedPregameSnapshotEvidence(
                source_reference_or_url="snapshot", raw_response_hash=HASH,
                retrieved_at_utc="invalid", available_at_utc="2026-09-07T16:00:00Z",
                canonical_keys={"game_pk": 823175, "player_mlbam_id": 123456},
            ),
            "INVALID_PAIRED_PREGAME_SNAPSHOT_PROVENANCE",
        ),
        (
            PairedPregameSnapshotEvidence(
                source_reference_or_url="snapshot", raw_response_hash=HASH,
                retrieved_at_utc="2026-09-07T16:00:00Z", available_at_utc="2026-09-07T16:00:00Z",
                canonical_keys={"game_pk": 823175, "player_mlbam_id": 999999},
            ),
            "INVALID_PAIRED_PREGAME_SNAPSHOT_PROVENANCE",
        ),
        (
            _paired_snapshot(retrieved_at_utc="2026-09-07T16:30:01Z"),
            "PAIRED_PREGAME_SNAPSHOT_RETRIEVED_AFTER_AS_OF_UTC",
        ),
        (
            _paired_snapshot(available_at_utc="2026-09-07T16:30:01Z"),
            "PAIRED_PREGAME_SNAPSHOT_AVAILABLE_AFTER_AS_OF_UTC",
        ),
        (
            _paired_snapshot(retrieved_at_utc="2026-09-07T17:00:00Z"),
            "PAIRED_PREGAME_SNAPSHOT_NOT_STRICTLY_PREGAME",
        ),
    ],
)
def test_invalid_or_post_cutoff_paired_pregame_snapshot_fails_closed(
    snapshot: PairedPregameSnapshotEvidence, reason: str,
) -> None:
    features = _ready_features()
    features["historical_labels"] = FeatureEvidence(
        endpoint_id="mlb_statsapi_box_scores", canonical_keys=_keys("historical_labels"),
        provenance=_provenance("2026-09-07T20:00:00Z"), status="READY",
        paired_pregame_snapshot=snapshot,
    )

    artifact = _evaluate(features)

    assert _blocker(artifact, "historical_labels").reason == reason


def test_readiness_artifact_is_strict_schema_and_has_no_probability_field() -> None:
    artifact = _evaluate(_ready_features()).to_dict()
    schema = json.loads((ROOT / "schemas" / "projection_readiness.schema.json").read_text())

    jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker()).validate(artifact)
    assert "probability" not in artifact
    assert artifact["scheduled_first_pitch_utc"] == "2026-09-07T17:00:00Z"
    assert artifact["model_publication_status"] == "MODEL_NOT_IMPLEMENTED"
    assert artifact["training_status"] == "READY"
    assert artifact["pregame_inference_status"] == "READY"


def test_schema_requires_utc_cutoff_fields_and_model_not_implemented_status() -> None:
    artifact = _evaluate(_ready_features()).to_dict()
    schema = json.loads((ROOT / "schemas" / "projection_readiness.schema.json").read_text())
    validator = jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker())

    missing_first_pitch = dict(artifact)
    del missing_first_pitch["scheduled_first_pitch_utc"]
    non_utc_as_of = dict(artifact) | {"as_of_utc": "2026-09-07T09:30:00-07:00"}
    non_utc_first_pitch = dict(artifact) | {"scheduled_first_pitch_utc": "2026-09-07T10:00:00-07:00"}
    published_model = dict(artifact) | {"model_publication_status": "MODEL_READY"}

    for invalid in (missing_first_pitch, non_utc_as_of, non_utc_first_pitch, published_model):
        with pytest.raises(jsonschema.ValidationError):
            validator.validate(invalid)


def test_projection_readiness_module_has_no_network_activity(monkeypatch) -> None:
    def network_attempt(*_args, **_kwargs):
        raise AssertionError("network activity is forbidden in readiness evaluation")

    monkeypatch.setattr(socket, "create_connection", network_attempt)
    assert _evaluate(_ready_features()).no_probability_status == "NO_PROBABILITY_CALCULATED"

    source = (ROOT / "src" / "hitterdna" / "projection_readiness.py").read_text()
    tree = ast.parse(source)

    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree) if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported.update(
        node.module.split(".")[0]
        for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module
    )
    assert not {"requests", "httpx", "urllib", "socket"} & imported
