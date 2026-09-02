"""Read-only Baseball Savant Expected Statistics leaderboard adapter."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timezone
import io
import json
from typing import Any, Callable, Literal, Mapping, Sequence

import requests

from hitterdna.filter_table import MetricObservation


SAVANT_EXPECTED_STATS_URL = (
    "https://baseballsavant.mlb.com/leaderboard/expected_statistics"
    "?type=batter&year={season}&csv=true"
)
SOURCE_NAME = "Baseball Savant Expected Statistics"
FETCH_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True)
class SavantExpectedStatsRow:
    player_mlbam_id: int | None
    player_name: str
    season: int
    pa: int | None
    batted_ball_events: int | None
    ba: float | None
    xba: float | None
    slg: float | None
    xslg: float | None
    woba: float | None
    xwoba: float | None
    source_url: str
    retrieved_at_utc: str


@dataclass(frozen=True)
class ExpectedStatsFetchResult:
    season: int
    source_url: str
    retrieved_at_utc: str
    rows: tuple[SavantExpectedStatsRow, ...]
    fetch_status: Literal["fetched", "unavailable", "malformed"]
    error_message: str | None


@dataclass(frozen=True)
class ExpectedStatsRawPayload:
    season: int
    source_url: str
    retrieved_at_utc: str
    payload: str | None
    fetch_status: Literal["fetched", "unavailable"]
    error_message: str | None


HttpGet = Callable[..., Any]


def expected_stats_source_url(season: int) -> str:
    """Return the reproducible public CSV URL for one requested season."""

    return SAVANT_EXPECTED_STATS_URL.format(season=season)


def fetch_expected_stats(season: int, http_get: HttpGet = requests.get) -> ExpectedStatsFetchResult:
    """Fetch and normalize one season's Savant expected-statistics rows."""

    raw_payload = fetch_expected_stats_csv_or_json(season, http_get=http_get)
    if raw_payload.fetch_status == "unavailable":
        return ExpectedStatsFetchResult(
            season=season,
            source_url=raw_payload.source_url,
            retrieved_at_utc=raw_payload.retrieved_at_utc,
            rows=(),
            fetch_status="unavailable",
            error_message=raw_payload.error_message,
        )
    assert raw_payload.payload is not None
    return parse_expected_stats_payload(
        season,
        raw_payload.payload,
        raw_payload.source_url,
        raw_payload.retrieved_at_utc,
    )


def fetch_expected_stats_csv_or_json(
    season: int, http_get: HttpGet = requests.get
) -> ExpectedStatsRawPayload:
    """Retrieve a public CSV or JSON body without parsing or persisting it."""

    source_url = expected_stats_source_url(season)
    retrieved_at_utc = _utc_now()
    try:
        response = http_get(source_url, timeout=FETCH_TIMEOUT_SECONDS)
    except (OSError, requests.RequestException):
        return ExpectedStatsRawPayload(
            season, source_url, retrieved_at_utc, None, "unavailable", "network request failed"
        )
    if getattr(response, "status_code", None) != 200:
        return ExpectedStatsRawPayload(
            season, source_url, retrieved_at_utc, None, "unavailable", "source returned non-200 status"
        )
    payload = getattr(response, "text", None)
    if not isinstance(payload, str):
        return ExpectedStatsRawPayload(
            season, source_url, retrieved_at_utc, None, "unavailable", "source response has no text body"
        )
    return ExpectedStatsRawPayload(season, source_url, retrieved_at_utc, payload, "fetched", None)


def parse_expected_stats_payload(
    season: int, payload: str | bytes | Mapping[str, Any] | Sequence[Mapping[str, Any]], source_url: str,
    retrieved_at_utc: str,
) -> ExpectedStatsFetchResult:
    """Purely parse CSV or JSON payload content into normalized source rows."""

    try:
        raw_rows = _payload_rows(payload)
    except (TypeError, ValueError, csv.Error, json.JSONDecodeError):
        return _malformed_result(season, source_url, retrieved_at_utc, "payload is not valid CSV or JSON rows")
    if not raw_rows:
        return _malformed_result(season, source_url, retrieved_at_utc, "payload contains no rows")

    rows: list[SavantExpectedStatsRow] = []
    for raw_row in raw_rows:
        try:
            normalized = normalize_expected_stats_row(raw_row, source_url, retrieved_at_utc, season)
        except ValueError:
            return _malformed_result(season, source_url, retrieved_at_utc, "row contains invalid numeric data")
        if normalized is not None:
            rows.append(normalized)
    if not rows:
        return _malformed_result(season, source_url, retrieved_at_utc, "payload has no structurally valid rows")
    valid_ids = [row.player_mlbam_id for row in rows if row.player_mlbam_id is not None]
    if len(valid_ids) != len(set(valid_ids)):
        return _malformed_result(season, source_url, retrieved_at_utc, "payload contains duplicate player MLBAM IDs")
    return ExpectedStatsFetchResult(season, source_url, retrieved_at_utc, tuple(rows), "fetched", None)


def normalize_expected_stats_row(
    raw_row: Mapping[str, Any], source_url: str, retrieved_at_utc: str, requested_season: int | None = None
) -> SavantExpectedStatsRow | None:
    """Normalize one structurally valid source row, preserving null metrics."""

    row = _normalized_keys(raw_row)
    player_name = _player_name(row)
    season = _integer_value(_first_value(row, ("season", "year")))
    if not player_name or season is None or requested_season is not None and season != requested_season:
        return None
    return SavantExpectedStatsRow(
        player_mlbam_id=_positive_integer(_first_value(row, ("player_id", "playerid", "player_mlbam_id", "mlbam_id", "id"))),
        player_name=player_name,
        season=season,
        pa=_integer_value(_first_value(row, ("pa", "plate_appearances"))),
        batted_ball_events=_integer_value(_first_value(row, ("bbe", "bip", "batted_ball_events"))),
        ba=_float_value(_first_value(row, ("ba", "batting_average"))),
        xba=_float_value(_first_value(row, ("est_ba", "xba", "x_ba", "expected_batting_average"))),
        slg=_float_value(_first_value(row, ("slg", "slugging"))),
        xslg=_float_value(_first_value(row, ("est_slg", "xslg", "x_slg", "expected_slugging"))),
        woba=_float_value(_first_value(row, ("woba", "weighted_on_base_average"))),
        xwoba=_float_value(_first_value(row, ("est_woba", "xwoba", "x_woba", "expected_woba"))),
        source_url=source_url,
        retrieved_at_utc=retrieved_at_utc,
    )


def expected_stats_to_observations(
    row: SavantExpectedStatsRow, retrieved_at_utc: str | None = None
) -> dict[str, MetricObservation]:
    """Convert raw expected-statistics fields to unverified audit observations."""

    observation_time = retrieved_at_utc or row.retrieved_at_utc
    return {
        "expected_batting_average": _observation(
            "expected_batting_average", row.xba, "pa", row.pa, "est_ba", row, observation_time
        ),
        "expected_slugging": _observation(
            "expected_slugging", row.xslg, "pa", row.pa, "est_slg", row, observation_time
        ),
        "expected_woba": _observation(
            "expected_woba", row.xwoba, "pa", row.pa, "est_woba", row, observation_time
        ),
        "plate_appearances": _observation(
            "plate_appearances", row.pa, "pa", row.pa, "pa", row, observation_time
        ),
        "batted_ball_events": _observation(
            "batted_ball_events", row.batted_ball_events, "bip", row.batted_ball_events,
            "bbe", row, observation_time
        ),
    }


def _payload_rows(payload: str | bytes | Mapping[str, Any] | Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8-sig")
    if isinstance(payload, str):
        content = payload.lstrip("\ufeff").strip()
        if not content:
            raise ValueError("empty payload")
        if content.startswith("{") or content.startswith("["):
            return _json_rows(json.loads(content))
        reader = csv.DictReader(io.StringIO(content))
        if not reader.fieldnames:
            raise ValueError("CSV has no header")
        return [dict(row) for row in reader]
    if isinstance(payload, Mapping) or isinstance(payload, Sequence):
        return _json_rows(payload)
    raise TypeError("unsupported payload")


def _json_rows(payload: Any) -> list[Mapping[str, Any]]:
    if isinstance(payload, Mapping):
        for key in ("data", "rows", "results"):
            candidate = payload.get(key)
            if isinstance(candidate, list):
                payload = candidate
                break
        else:
            raise ValueError("JSON object has no rows collection")
    if not isinstance(payload, list) or not all(isinstance(row, Mapping) for row in payload):
        raise ValueError("JSON rows are malformed")
    return list(payload)


def _normalized_keys(raw_row: Mapping[str, Any]) -> dict[str, Any]:
    return {str(key).strip().casefold(): value for key, value in raw_row.items()}


def _first_value(row: Mapping[str, Any], aliases: tuple[str, ...]) -> Any:
    for alias in aliases:
        if alias in row:
            return row[alias]
    return None


def _player_name(row: Mapping[str, Any]) -> str | None:
    name = _text_value(_first_value(row, ("player_name", "name", "player")))
    if name:
        return name
    first_name = _text_value(_first_value(row, ("first_name", "firstname")))
    last_name = _text_value(_first_value(row, ("last_name", "lastname")))
    return f"{first_name} {last_name}" if first_name and last_name else None


def _text_value(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped if stripped and stripped.casefold() not in {"null", "none", "n/a", "na"} else None


def _integer_value(value: Any) -> int | None:
    text = _text_value(value) if isinstance(value, str) else value
    if text is None:
        return None
    if isinstance(text, bool):
        raise ValueError("boolean is not an integer")
    if isinstance(text, int):
        return text
    if isinstance(text, float) and text.is_integer():
        return int(text)
    if isinstance(text, str) and text.lstrip("+-").isdigit():
        return int(text)
    raise ValueError("invalid integer")


def _positive_integer(value: Any) -> int | None:
    try:
        numeric = _integer_value(value)
    except ValueError:
        return None
    return numeric if numeric is not None and numeric > 0 else None


def _float_value(value: Any) -> float | None:
    text = _text_value(value) if isinstance(value, str) else value
    if text is None:
        return None
    if isinstance(text, bool):
        raise ValueError("boolean is not a number")
    if isinstance(text, (int, float)):
        return float(text)
    if isinstance(text, str):
        try:
            return float(text)
        except ValueError as error:
            raise ValueError("invalid float") from error
    raise ValueError("invalid float")


def _observation(
    metric_key: str,
    value: float | int | None,
    sample_type: str,
    sample_n: int | None,
    source_field: str,
    row: SavantExpectedStatsRow,
    retrieved_at_utc: str,
) -> MetricObservation:
    return MetricObservation(
        metric_key=metric_key,
        value=value,
        sample_type=sample_type,
        sample_n=sample_n,
        source_url=row.source_url,
        retrieved_at_utc=retrieved_at_utc,
        source_name=SOURCE_NAME,
        stabilization_status="unverified",
        notes=f"source field: {source_field}; stabilization evaluation deferred to the stabilization layer",
    )


def _malformed_result(
    season: int, source_url: str, retrieved_at_utc: str, error_message: str
) -> ExpectedStatsFetchResult:
    return ExpectedStatsFetchResult(season, source_url, retrieved_at_utc, (), "malformed", error_message)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
