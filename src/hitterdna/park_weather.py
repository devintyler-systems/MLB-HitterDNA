"""Pure, fail-closed park and weather input normalization."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from math import cos, isfinite, radians
from typing import Any, Literal, Mapping


WeatherKind = Literal["forecast", "observed"]
RoofState = Literal["open", "closed", "unknown", "not_applicable"]
WindNormalizationStatus = Literal["PASS", "OMITTED"]
ParkFactorStatus = Literal["PASS", "UNVERIFIED"]


@dataclass(frozen=True)
class ParkWeatherSnapshot:
    """One source-attributed weather snapshot plus an optional park bearing."""

    game_pk: int | None = None
    venue_mlbam_id: int | None = None
    weather_kind: WeatherKind | None = None
    valid_at_utc: str | None = None
    retrieved_at_utc: str | None = None
    temperature_f: int | float | None = None
    wind_speed_mph: int | float | None = None
    wind_from_deg: int | float | None = None
    roof_state: RoofState | None = None
    provider: str | None = None
    provider_location_key: str | None = None
    source_request: str | None = None
    raw_response_hash: str | None = None
    center_field_bearing_deg: int | float | None = None


@dataclass(frozen=True)
class WindNormalizationResult:
    """The auditable outcome of bearing-relative wind normalization."""

    wind_normalization_status: WindNormalizationStatus
    outward_wind_mph: float | None
    omission_reason: str | None


@dataclass(frozen=True)
class ParkFactorDecision:
    """A park-factor exception decision that retains continuity metadata."""

    park_factor_status: ParkFactorStatus
    reason: str | None
    rays_continuity_suspect: bool


def normalize_wind(
    snapshot: ParkWeatherSnapshot | Mapping[str, Any],
    center_field_bearing_deg: int | float | None = None,
) -> WindNormalizationResult:
    """Return the deterministic outward component or an explicit omission."""

    venue_mlbam_id = _snapshot_value(snapshot, "venue_mlbam_id")
    wind_speed_mph = _snapshot_value(snapshot, "wind_speed_mph")
    wind_from_deg = _snapshot_value(snapshot, "wind_from_deg")
    bearing = (
        center_field_bearing_deg
        if center_field_bearing_deg is not None
        else _snapshot_value(snapshot, "center_field_bearing_deg")
    )
    roof_state = _snapshot_value(snapshot, "roof_state")

    if not _is_positive_integer(venue_mlbam_id):
        return _omitted("missing_or_invalid_venue_identity")
    if not _is_nonnegative_finite_number(wind_speed_mph):
        return _omitted("missing_or_invalid_wind_speed")
    if not _is_bearing(wind_from_deg):
        return _omitted("missing_or_invalid_wind_direction")
    if not _is_bearing(bearing):
        return _omitted("missing_or_invalid_center_field_bearing")
    if roof_state not in {"open", "closed", "unknown", "not_applicable"}:
        return _omitted("missing_or_invalid_roof_state")
    if roof_state == "closed":
        return _omitted("roof_closed")
    if roof_state == "unknown":
        return _omitted("roof_unknown")
    if not _is_utc_timestamp(_snapshot_value(snapshot, "valid_at_utc")):
        return _omitted("missing_or_invalid_valid_timestamp")
    if not _has_source_provenance(snapshot):
        return _omitted("missing_or_invalid_source_provenance")

    wind_toward_deg = (wind_from_deg + 180) % 360
    outward_wind_mph = wind_speed_mph * cos(radians(wind_toward_deg - bearing))
    return WindNormalizationResult("PASS", float(outward_wind_mph), None)


def evaluate_park_factor_status(
    venue_name: str | None,
    *,
    park_factor_status: ParkFactorStatus = "UNVERIFIED",
    rays_continuity_suspect: bool = False,
) -> ParkFactorDecision:
    """Apply only declared park exceptions without inferring factor evidence."""

    if isinstance(venue_name, str) and venue_name.strip().casefold() == "sutter health park":
        return ParkFactorDecision("UNVERIFIED", "sutter_health_unverified", rays_continuity_suspect)
    reason = None if park_factor_status == "PASS" else "park_factor_not_supplied"
    return ParkFactorDecision(park_factor_status, reason, rays_continuity_suspect)


def _snapshot_value(snapshot: ParkWeatherSnapshot | Mapping[str, Any], field_name: str) -> Any:
    if isinstance(snapshot, Mapping):
        return snapshot.get(field_name)
    return getattr(snapshot, field_name, None)


def _omitted(reason: str) -> WindNormalizationResult:
    return WindNormalizationResult("OMITTED", None, reason)


def _has_source_provenance(snapshot: ParkWeatherSnapshot | Mapping[str, Any]) -> bool:
    string_fields = ("provider", "provider_location_key", "source_request", "raw_response_hash")
    if not all(_is_nonempty_string(_snapshot_value(snapshot, field)) for field in string_fields):
        return False
    return _is_utc_timestamp(_snapshot_value(snapshot, "retrieved_at_utc"))


def _is_nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_nonnegative_finite_number(value: Any) -> bool:
    return _is_finite_number(value) and value >= 0


def _is_bearing(value: Any) -> bool:
    return _is_finite_number(value) and 0 <= value < 360


def _is_finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value)


def _is_positive_integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def _is_utc_timestamp(value: Any) -> bool:
    if (
        not isinstance(value, str)
        or "T" not in value
        or not value.endswith(("Z", "+00:00"))
    ):
        return False

    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False

    return (
        parsed.tzinfo is not None
        and parsed.utcoffset() is not None
        and parsed.utcoffset().total_seconds() == 0
    )
