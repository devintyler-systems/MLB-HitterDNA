"""Fail-closed loader for the committed source endpoint registry."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Literal, Mapping


CANONICAL_KEYS = frozenset({"game_pk", "player_mlbam_id", "opponent_pitcher_mlbam_id", "venue_mlbam_id", "season", "game_date"})
DENOMINATORS = frozenset({"plate_appearances", "at_bats", "balls_in_play", "batted_ball_events", "pitches_seen", "batters_faced", "swings"})
FRESHNESS_STATUSES = frozenset({"PASS", "STALE", "UNVERIFIED"})
FAILURE_STATUSES = frozenset({"PASS", "RETRYABLE", "TERMINAL", "FAIL", "UNVERIFIED"})
IMPLEMENTATION_STATUSES = frozenset({"AUTOMATED", "CONTRACT_ONLY", "NON_AUTOMATED", "DEFERRED"})
ENTRY_FIELDS = frozenset({"endpoint_id", "source", "domain", "purpose", "endpoint_template", "method", "parameter_contract", "response_format", "canonical_keys", "retrieval_cadence", "freshness_ttl_minutes", "pagination_contract", "row_limit", "chunking_contract", "provenance_fields", "exception_policy", "implementation_status"})
AUTOMATED_IDS = frozenset({"mlb_statsapi_schedule_by_date", "mlb_statsapi_live_game_feed", "mlb_statsapi_lineup_confirmation", "mlb_statsapi_probable_pitchers", "savant_expected_statistics"})
CONTRACT_ONLY_IDS = frozenset({"mlb_statsapi_player_identity", "mlb_statsapi_venue_identity", "mlb_statsapi_transactions_il_context", "mlb_statsapi_box_scores", "savant_statcast_search_csv"})
NON_AUTOMATED_IDS = frozenset({"savant_custom_leaderboards", "savant_pitch_arsenal_batter", "savant_statcast_park_factors", "savant_bat_tracking", "savant_swing_path_attack_angle", "fangraphs_the_bat_x", "fangraphs_steamer", "fangraphs_atc", "fangraphs_depth_charts", "fangraphs_roster_resource"})
DEFERRED_IDS = frozenset({"deferred_weather_interface", "deferred_market_interface"})
UNIVERSAL_PROVENANCE_FIELDS = frozenset({"source", "endpoint_or_url", "submitted_parameters", "retrieved_at_utc", "raw_response_hash", "row_count_when_relevant", "canonical_keys", "source_freshness_status", "source_failure_status"})


class RegistryValidationError(ValueError):
    """Raised when an entry cannot safely be used."""


@dataclass(frozen=True)
class SourceEndpointEntry:
    endpoint_id: str
    source: str
    domain: str
    purpose: str
    endpoint_template: str | None
    method: Literal["GET", "NONE"]
    parameter_contract: Mapping[str, Any]
    response_format: Mapping[str, str]
    canonical_keys: Mapping[str, Any]
    retrieval_cadence: Mapping[str, str]
    freshness_ttl_minutes: None
    pagination_contract: Mapping[str, Any]
    row_limit: int | None
    chunking_contract: Mapping[str, Any]
    provenance_fields: Mapping[str, bool]
    exception_policy: Mapping[str, Any]
    implementation_status: Literal["AUTOMATED", "CONTRACT_ONLY", "NON_AUTOMATED", "DEFERRED"]


@dataclass(frozen=True)
class StatcastSearchCapResult:
    promotion_allowed: bool
    subdivision_required: bool
    reason: str | None


def default_registry_path() -> Path:
    return Path(__file__).resolve().parents[2] / "config" / "source_endpoint_registry.json"


def load_source_endpoint_registry(path: Path | None = None) -> tuple[SourceEndpointEntry, ...]:
    """Load the committed document only when every entry passes strict checks."""

    registry_path = path or default_registry_path()
    try:
        document = json.loads(registry_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RegistryValidationError(f"registry cannot be read: {error}") from error
    if not isinstance(document, dict) or set(document) != {"registry_version", "entries"}:
        raise RegistryValidationError("registry document fields are invalid")
    if document["registry_version"] != "1" or not isinstance(document["entries"], list):
        raise RegistryValidationError("registry document version or entries are invalid")
    entries = tuple(_validate_entry(entry) for entry in document["entries"])
    if len({entry.endpoint_id for entry in entries}) != len(entries):
        raise RegistryValidationError("endpoint IDs must be unique")
    _validate_inventory(entries)
    return entries


def automation_ready_entries(path: Path | None = None) -> tuple[SourceEndpointEntry, ...]:
    """Return only documented adapter-backed entries; manual/deferred entries never qualify."""

    return tuple(entry for entry in load_source_endpoint_registry(path) if entry.implementation_status == "AUTOMATED")


def validate_source_health_statuses(freshness: str, failure: str) -> None:
    """Reject unknown or collapsed source-health state values."""

    if freshness not in FRESHNESS_STATUSES:
        raise RegistryValidationError("unknown source_freshness_status")
    if failure not in FAILURE_STATUSES:
        raise RegistryValidationError("unknown source_failure_status")


def validate_denominator_definition(declared: str, observed: str) -> None:
    """Require an exact source denominator; no baseball shorthand is interchangeable."""

    if declared not in DENOMINATORS or observed not in DENOMINATORS or declared != observed:
        raise RegistryValidationError("denominator substitution is not permitted")


def evaluate_statcast_search_response(returned_row_count: int) -> StatcastSearchCapResult:
    """Flag the documented hard cap; it is not pagination and cannot be promoted."""

    if isinstance(returned_row_count, bool) or not isinstance(returned_row_count, int) or returned_row_count < 0:
        raise RegistryValidationError("returned row count must be a non-negative integer")
    if returned_row_count >= 30000:
        return StatcastSearchCapResult(False, True, "row_cap_requires_subdivision")
    return StatcastSearchCapResult(True, False, None)


def _validate_entry(raw: Any) -> SourceEndpointEntry:
    if not isinstance(raw, dict) or set(raw) != ENTRY_FIELDS:
        raise RegistryValidationError("entry must contain exactly the required fields")
    if not isinstance(raw["endpoint_id"], str) or not raw["endpoint_id"]:
        raise RegistryValidationError("invalid endpoint_id")
    if raw["source"] not in {"MLB Stats API", "Baseball Savant", "FanGraphs", "Deferred interface"}:
        raise RegistryValidationError("unsupported source")
    if raw["domain"] not in {"schedule", "game", "lineup", "pitcher", "identity", "transactions", "boxscore", "expected_statistics", "custom_leaderboard", "pitch_arsenal", "statcast_search", "park_factors", "bat_tracking", "swing_path", "projection", "roster", "weather", "market"} or not isinstance(raw["purpose"], str) or not raw["purpose"]:
        raise RegistryValidationError("invalid entry domain or purpose")
    if raw["implementation_status"] not in IMPLEMENTATION_STATUSES:
        raise RegistryValidationError("invalid capability state")
    _validate_endpoint_mode(raw)
    _validate_parameter_contract(raw["parameter_contract"])
    _validate_response_format(raw["response_format"])
    _validate_canonical_keys(raw["canonical_keys"])
    _validate_cadence(raw["retrieval_cadence"])
    _validate_provenance(raw["provenance_fields"])
    _validate_ttl(raw)
    _validate_limits(raw)
    _validate_exception_policy(raw["exception_policy"])
    return SourceEndpointEntry(
        endpoint_id=raw["endpoint_id"], source=raw["source"], domain=raw["domain"], purpose=raw["purpose"],
        endpoint_template=raw["endpoint_template"],
        method=raw["method"], parameter_contract=raw["parameter_contract"], response_format=raw["response_format"],
        canonical_keys=raw["canonical_keys"], retrieval_cadence=raw["retrieval_cadence"], freshness_ttl_minutes=None,
        pagination_contract=raw["pagination_contract"], row_limit=raw["row_limit"], chunking_contract=raw["chunking_contract"],
        provenance_fields=raw["provenance_fields"], exception_policy=raw["exception_policy"], implementation_status=raw["implementation_status"],
    )


def _validate_endpoint_mode(raw: Mapping[str, Any]) -> None:
    status = raw["implementation_status"]
    endpoint = raw["endpoint_template"]
    response_format = raw["response_format"]
    if status in {"NON_AUTOMATED", "DEFERRED"}:
        if endpoint is not None or raw["method"] != "NONE":
            raise RegistryValidationError("manual or deferred entry has an automation endpoint")
        expected = "MANUAL_EXPORT" if status == "NON_AUTOMATED" else "DEFERRED_INTERFACE"
        if not isinstance(response_format, dict) or response_format.get("format") != expected:
            raise RegistryValidationError("manual or deferred response format is invalid")
    elif raw["method"] != "GET" or endpoint is None and "request_variants" not in raw["parameter_contract"]:
        raise RegistryValidationError("network contract has no documented GET endpoint")
    if not (isinstance(endpoint, str) or endpoint is None):
        raise RegistryValidationError("guessed endpoint representation")
    if isinstance(endpoint, str) and not endpoint.startswith("https://"):
        raise RegistryValidationError("guessed endpoint")


def _validate_parameter_contract(contract: Any) -> None:
    expected = {"path_parameters", "query_parameters", "required_context", "preserve_submitted_parameters", "denominator_definitions", "extensions"}
    if "request_variants" in contract:
        expected.add("request_variants")
    if not isinstance(contract, dict) or set(contract) != expected or contract["preserve_submitted_parameters"] is not True:
        raise RegistryValidationError("invalid parameter contract")
    for field in ("path_parameters", "query_parameters", "required_context"):
        _validate_parameters(contract[field], field)
    if "request_variants" in contract:
        _validate_variants(contract["request_variants"])
    denominators = contract["denominator_definitions"]
    if not isinstance(denominators, list) or not set(denominators).issubset(DENOMINATORS) or len(denominators) != len(set(denominators)):
        raise RegistryValidationError("denominator declaration is invalid")
    _validate_extensions(contract["extensions"])


def _validate_parameters(items: Any, field: str) -> None:
    if not isinstance(items, list) or len({item.get("name") for item in items if isinstance(item, dict)}) != len(items):
        raise RegistryValidationError(f"invalid {field}")
    for item in items:
        if not isinstance(item, dict) or set(item) != {"name", "required", "type", "constraint"} or not isinstance(item["name"], str) or not item["name"] or not isinstance(item["required"], bool) or item["type"] not in {"integer", "string", "boolean"}:
            raise RegistryValidationError(f"invalid {field}")
        constraint = item["constraint"]
        if not isinstance(constraint, dict) or set(constraint) - {"kind", "value", "values"} or constraint.get("kind") not in {"const", "enum", "template", "positive_integer", "text"}:
            raise RegistryValidationError(f"invalid {field} constraint")
        if constraint["kind"] == "enum" and (set(constraint) != {"kind", "values"} or not isinstance(constraint["values"], list) or not constraint["values"]):
            raise RegistryValidationError(f"invalid {field} enum")
        if constraint["kind"] in {"const", "template"} and set(constraint) != {"kind", "value"}:
            raise RegistryValidationError(f"invalid {field} constraint")
        if constraint["kind"] in {"positive_integer", "text"} and set(constraint) != {"kind"}:
            raise RegistryValidationError(f"invalid {field} constraint")


def _validate_variants(items: Any) -> None:
    if not isinstance(items, list) or len(items) < 2:
        raise RegistryValidationError("malformed request variant")
    for item in items:
        if not isinstance(item, dict) or set(item) != {"endpoint_template", "method", "path_parameters", "query_parameters", "selection_rule"} or not isinstance(item["endpoint_template"], str) or not item["endpoint_template"].startswith("https://") or item["method"] != "GET" or not isinstance(item["selection_rule"], str) or not item["selection_rule"]:
            raise RegistryValidationError("malformed request variant")
        _validate_parameters(item["path_parameters"], "variant path_parameters")
        _validate_parameters(item["query_parameters"], "variant query_parameters")


def validate_request_parameters(entry: SourceEndpointEntry, path_parameters: Mapping[str, Any], query_parameters: Mapping[str, Any]) -> None:
    """Validate submitted request parameters against one single-route contract."""
    if "request_variants" in entry.parameter_contract:
        raise RegistryValidationError("select and validate a request variant explicitly")
    _validate_submitted(entry.parameter_contract["path_parameters"], path_parameters)
    _validate_submitted(entry.parameter_contract["query_parameters"], query_parameters)


def _validate_submitted(declared: Any, submitted: Mapping[str, Any]) -> None:
    if not isinstance(submitted, Mapping) or set(submitted) - {item["name"] for item in declared}:
        raise RegistryValidationError("unknown request parameter")
    for item in declared:
        name, value = item["name"], submitted.get(item["name"])
        if item["required"] and name not in submitted: raise RegistryValidationError("missing required request parameter")
        if name not in submitted: continue
        if item["type"] == "integer" and (isinstance(value, bool) or not isinstance(value, int)): raise RegistryValidationError("wrong request parameter type")
        if item["type"] == "string" and not isinstance(value, str): raise RegistryValidationError("wrong request parameter type")
        c=item["constraint"]
        if c["kind"] == "const" and value != c["value"] or c["kind"] == "enum" and value not in c["values"] or c["kind"] == "positive_integer" and value <= 0: raise RegistryValidationError("invalid request parameter value")


def _validate_response_format(value: Any) -> None:
    if not isinstance(value, dict) or set(value) != {"format", "response_type"}:
        raise RegistryValidationError("malformed response format")
    if value["format"] not in {"JSON", "CSV", "MANUAL_EXPORT", "DEFERRED_INTERFACE"} or value["response_type"] not in {"OBJECT", "TABULAR", "INTERFACE"}:
        raise RegistryValidationError("malformed response format")
    if (value["format"] == "JSON") != (value["response_type"] == "OBJECT") or (value["format"] in {"CSV", "MANUAL_EXPORT"}) != (value["response_type"] == "TABULAR") or (value["format"] == "DEFERRED_INTERFACE") != (value["response_type"] == "INTERFACE"):
        raise RegistryValidationError("response format contract contradicts response type")


def _validate_canonical_keys(value: Any) -> None:
    if not isinstance(value, dict) or set(value) != {"required", "supplemental"}:
        raise RegistryValidationError("invalid canonical keys")
    required = value["required"]
    if not isinstance(required, list) or not required or not set(required).issubset(CANONICAL_KEYS) or len(required) != len(set(required)):
        raise RegistryValidationError("missing or unapproved canonical keys")
    supplemental = value["supplemental"]
    if not isinstance(supplemental, list):
        raise RegistryValidationError("invalid supplemental canonical keys")
    for item in supplemental:
        if not isinstance(item, dict) or set(item) != {"key", "justification"} or not all(isinstance(item[field], str) and item[field] for field in item):
            raise RegistryValidationError("unapproved supplemental key declaration")


def validate_conditional_canonical_keys(entry: SourceEndpointEntry, *, date_bounded: bool, game_level: bool, response_keys: set[str]) -> None:
    """Require game_date for date-bounded or game-level bat-tracking records."""
    if entry.endpoint_id in {"savant_bat_tracking", "savant_swing_path_attack_angle"} and (date_bounded or game_level) and "game_date" not in response_keys:
        raise RegistryValidationError("conditional game_date is required")


def _validate_provenance(value: Any) -> None:
    if not isinstance(value, dict) or set(value) != UNIVERSAL_PROVENANCE_FIELDS or any(item is not True for item in value.values()):
        raise RegistryValidationError("universal provenance is incomplete")


def _validate_ttl(raw: Mapping[str, Any]) -> None:
    if raw["freshness_ttl_minutes"] is not None or "numeric_ttl_not_specified_in_source_contract" not in raw["exception_policy"].get("fail_closed_reasons", []):
        raise RegistryValidationError("numeric freshness TTL is not authorized")


def _validate_limits(raw: Mapping[str, Any]) -> None:
    row_limit, pagination, chunk = raw["row_limit"], raw["pagination_contract"], raw["chunking_contract"]
    if row_limit is not None and (isinstance(row_limit, bool) or not isinstance(row_limit, int) or row_limit < 1):
        raise RegistryValidationError("invalid row limit")
    if not isinstance(pagination, dict) or set(pagination) != {"mode", "cap_is_not_pagination"}:
        raise RegistryValidationError("invalid pagination contract")
    if pagination["mode"] not in {"NONE", "NOT_PAGINATION"} or not isinstance(pagination["cap_is_not_pagination"], bool):
        raise RegistryValidationError("invalid pagination contract")
    if not isinstance(chunk, dict) or set(chunk) != {"enabled", "max_calendar_days", "inclusive_start_date", "inclusive_end_date", "at_cap_action", "promotion_requirements"}:
        raise RegistryValidationError("invalid chunking contract")
    if not isinstance(chunk["enabled"], bool) or chunk["max_calendar_days"] is not None and (isinstance(chunk["max_calendar_days"], bool) or not isinstance(chunk["max_calendar_days"], int) or chunk["max_calendar_days"] < 1) or not isinstance(chunk["inclusive_start_date"], bool) or not isinstance(chunk["inclusive_end_date"], bool) or chunk["at_cap_action"] not in {"NOT_APPLICABLE", "SUBDIVIDE_BEFORE_PROMOTION"} or not isinstance(chunk["promotion_requirements"], list):
        raise RegistryValidationError("invalid chunking contract")
    if raw["endpoint_id"] == "savant_statcast_search_csv":
        if row_limit != 30000 or pagination != {"mode": "NOT_PAGINATION", "cap_is_not_pagination": True} or chunk["enabled"] is not True or chunk["max_calendar_days"] != 5 or chunk["inclusive_start_date"] is not True or chunk["inclusive_end_date"] is not True or chunk["at_cap_action"] != "SUBDIVIDE_BEFORE_PROMOTION" or set(chunk["promotion_requirements"]) != {"compatible_response_types", "compatible_event_taxonomies", "compatible_column_contracts", "compatible_denominator_definitions"}:
            raise RegistryValidationError("Statcast Search row-limit or chunking contract is invalid")


def _validate_exception_policy(value: Any) -> None:
    if not isinstance(value, dict) or set(value) != {"fail_closed_reasons", "metadata", "extensions"} or not isinstance(value["fail_closed_reasons"], list) or not value["fail_closed_reasons"]:
        raise RegistryValidationError("invalid exception policy")
    _validate_extensions(value["metadata"])
    _validate_extensions(value["extensions"])


def _validate_cadence(value: Any) -> None:
    if not isinstance(value, dict) or set(value) != {"trigger", "description"} or not all(isinstance(value[field], str) and value[field] for field in value):
        raise RegistryValidationError("invalid retrieval cadence")


def _validate_extensions(items: Any) -> None:
    if not isinstance(items, list):
        raise RegistryValidationError("extensions must be a list")
    for item in items:
        if not isinstance(item, dict) or set(item) != {"name", "value", "justification"} or not isinstance(item["name"], str) or not item["name"] or not isinstance(item["justification"], str) or not item["justification"]:
            raise RegistryValidationError("extension is not controlled")


def _validate_inventory(entries: tuple[SourceEndpointEntry, ...]) -> None:
    observed = {entry.endpoint_id: entry.implementation_status for entry in entries}
    expected = {**{key: "AUTOMATED" for key in AUTOMATED_IDS}, **{key: "CONTRACT_ONLY" for key in CONTRACT_ONLY_IDS}, **{key: "NON_AUTOMATED" for key in NON_AUTOMATED_IDS}, **{key: "DEFERRED" for key in DEFERRED_IDS}}
    if observed != expected:
        raise RegistryValidationError("registry inventory or capability assignment is invalid")
