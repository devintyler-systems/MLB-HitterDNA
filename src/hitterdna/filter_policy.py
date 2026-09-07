"""Local, integrity-checked loader for the authoritative filter policy."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping

from hitterdna.filter_table import FilterDefinition, filter_definition_problem


POLICY_MARKER = re.compile(
    r"<!-- HITTERDNA_FILTER_POLICY_BEGIN -->\s*```json\s*(\{.*?\})\s*```\s*"
    r"<!-- HITTERDNA_FILTER_POLICY_END -->",
    re.DOTALL,
)
DEFAULT_POLICY_REFERENCE = "docs/filter-thresholds.md"


@dataclass(frozen=True)
class FilterPolicyLoadResult:
    load_status: str
    reason: str | None
    contract: dict[str, Any] | None


def load_filter_policy_contract(
    fixture_filter: object | None = None,
    *,
    loaded_at_utc: str | None = None,
    project_root: Path | None = None,
) -> FilterPolicyLoadResult:
    """Load one local policy document and verify any fixture-recorded identity."""

    root = project_root or Path(__file__).resolve().parents[2]
    if fixture_filter is not None and not isinstance(fixture_filter, Mapping):
        return _failure("FILTER_POLICY_MALFORMED: fixture filter contract is not an object")
    recorded = dict(fixture_filter or {})
    reference = recorded.get("policy_source_reference", DEFAULT_POLICY_REFERENCE)
    if not isinstance(reference, str) or not reference.strip():
        return _failure("FILTER_POLICY_MALFORMED: policy_source_reference is missing")
    source_path = _resolve_source_path(reference, root)
    try:
        content = source_path.read_bytes()
    except OSError:
        return _failure("FILTER_POLICY_MISSING: policy source is unreadable")
    content_hash = hashlib.sha256(content).hexdigest()
    recorded_hash = recorded.get("policy_content_sha256")
    if recorded_hash is not None and recorded_hash != content_hash:
        return _failure("FILTER_POLICY_HASH_MISMATCH: recorded policy hash does not match source bytes")
    try:
        document = content.decode("utf-8")
        match = POLICY_MARKER.search(document)
        if match is None:
            raise ValueError("machine-readable policy block is missing")
        root_payload = json.loads(match.group(1))
        policy = root_payload["filter_policy"]
    except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
        return _failure(f"FILTER_POLICY_MALFORMED: {error}")
    if not isinstance(policy, Mapping):
        return _failure("FILTER_POLICY_MALFORMED: filter_policy is not an object")
    version = policy.get("policy_version")
    if not isinstance(version, str) or not version:
        return _failure("FILTER_POLICY_MALFORMED: policy_version is missing")
    if recorded.get("policy_version") not in {None, version}:
        return _failure("FILTER_POLICY_VERSION_MISMATCH: recorded policy version does not match source")
    definitions = policy.get("definitions")
    thresholds = policy.get("thresholds")
    if not isinstance(definitions, list) or not definitions:
        return _failure("FILTER_POLICY_NO_REQUIRED_DEFINITIONS: definitions are missing")
    if not isinstance(thresholds, Mapping):
        return _failure("FILTER_POLICY_MALFORMED: thresholds is not an object")
    normalized_definitions: list[dict[str, Any]] = []
    ids: set[str] = set()
    required_count = 0
    for raw in definitions:
        if not isinstance(raw, Mapping):
            return _failure("FILTER_POLICY_MALFORMED: filter definition is not an object")
        try:
            definition = FilterDefinition(**raw)
        except TypeError as error:
            return _failure(f"FILTER_POLICY_MALFORMED: {error}")
        problem = filter_definition_problem(definition)
        if problem:
            return _failure(f"FILTER_POLICY_MALFORMED: {problem}")
        if definition.filter_version != version:
            return _failure("FILTER_POLICY_MALFORMED: definition version does not match policy version")
        if definition.filter_id in ids:
            return _failure("FILTER_POLICY_MALFORMED: duplicate filter_id")
        ids.add(definition.filter_id)
        if definition.required:
            required_count += 1
        if definition.operator in {"gte", "gt", "lte", "lt", "eq"}:
            if definition.threshold_ref not in thresholds:
                return _failure("FILTER_POLICY_REQUIRED_THRESHOLD_MISSING: threshold reference is absent")
        normalized_definitions.append(asdict(definition))
    if required_count == 0:
        return _failure("FILTER_POLICY_NO_REQUIRED_DEFINITIONS: no required definitions")
    timestamp = loaded_at_utc or _utc_now()
    if not _is_utc_timestamp(timestamp):
        return _failure("FILTER_POLICY_MALFORMED: loaded timestamp is not UTC")
    return FilterPolicyLoadResult("loaded", None, {
        "policy_source_reference": reference,
        "policy_content_sha256": content_hash,
        "policy_loaded_at_utc": timestamp,
        "policy_version": version,
        "definitions": normalized_definitions,
        "thresholds": dict(thresholds),
    })


def _resolve_source_path(reference: str, root: Path) -> Path:
    candidate = Path(reference)
    return candidate if candidate.is_absolute() else root / candidate


def _failure(reason: str) -> FilterPolicyLoadResult:
    return FilterPolicyLoadResult("unverified", reason, None)


def _is_utc_timestamp(value: object) -> bool:
    if not isinstance(value, str) or not value.endswith(("Z", "+00:00")):
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
