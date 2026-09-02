"""Source-attributed, fail-closed stabilization policy loading and evaluation."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
import json
from math import isfinite
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence
from urllib.parse import urlparse

from hitterdna.filter_table import MetricObservation


StabilizationStatus = Literal["pass", "fail", "unverified", "not_applicable"]
PolicyLoadStatus = Literal["loaded", "malformed"]

_POLICY_FIELDS = frozenset(
    {
        "policy_id",
        "policy_version",
        "metric_key",
        "sample_type",
        "minimum_sample_ref",
        "minimum_sample_value",
        "source_name",
        "source_url",
        "retrieved_at_utc",
        "rationale",
        "applies_from_season",
        "applies_through_season",
    }
)
_TOP_LEVEL_FIELDS = frozenset({"policy_file_version", "policies"})


@dataclass(frozen=True)
class StabilizationPolicy:
    """One externally sourced minimum-sample policy with full provenance."""

    policy_id: str
    policy_version: str
    metric_key: str
    sample_type: str
    minimum_sample_ref: str
    minimum_sample_value: int | float
    source_name: str
    source_url: str
    retrieved_at_utc: str
    rationale: str
    applies_from_season: int | None
    applies_through_season: int | None

    def __post_init__(self) -> None:
        for field_name in (
            "policy_id",
            "policy_version",
            "metric_key",
            "sample_type",
            "minimum_sample_ref",
            "source_name",
            "source_url",
            "retrieved_at_utc",
            "rationale",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value:
                raise ValueError(f"{field_name} must be a non-empty string")
        if not _is_positive_finite_number(self.minimum_sample_value):
            raise ValueError("minimum_sample_value must be a positive finite number")
        if not _is_source_url(self.source_url):
            raise ValueError("source_url must be an absolute HTTP(S) URL")
        if not _is_utc_timestamp(self.retrieved_at_utc):
            raise ValueError("retrieved_at_utc must be an ISO-8601 UTC timestamp")
        if not _is_season_or_none(self.applies_from_season):
            raise ValueError("applies_from_season must be an integer or null")
        if not _is_season_or_none(self.applies_through_season):
            raise ValueError("applies_through_season must be an integer or null")
        if (
            self.applies_from_season is not None
            and self.applies_through_season is not None
            and self.applies_from_season > self.applies_through_season
        ):
            raise ValueError("season window is invalid")


@dataclass(frozen=True)
class StabilizationDecision:
    """One auditable stabilization decision without any metric-value judgment."""

    metric_key: str
    sample_type: str | None
    sample_n: int | float | None
    policy_id: str | None
    policy_version: str | None
    minimum_sample_ref: str | None
    minimum_sample_value: int | float | None
    source_name: str | None
    source_url: str | None
    retrieved_at_utc: str | None
    status: StabilizationStatus
    reason: str


@dataclass(frozen=True, init=False)
class StabilizationRegistry:
    """Immutable policy collection with deterministic, fail-closed resolution."""

    policies: tuple[StabilizationPolicy, ...]

    def __init__(self, policies: Sequence[StabilizationPolicy]) -> None:
        policy_tuple = tuple(policies)
        if not all(isinstance(policy, StabilizationPolicy) for policy in policy_tuple):
            raise TypeError("registry policies must be StabilizationPolicy objects")
        identifiers = {(policy.policy_id, policy.policy_version) for policy in policy_tuple}
        if len(identifiers) != len(policy_tuple):
            raise ValueError("duplicate policy_id and policy_version")
        object.__setattr__(self, "policies", policy_tuple)

    def resolve(
        self, metric_key: str, sample_type: str, season: int | None = None
    ) -> StabilizationPolicy | None:
        """Resolve exactly one applicable policy without insertion-order fallback."""

        exact_matches = tuple(
            policy
            for policy in self.policies
            if policy.metric_key == metric_key and policy.sample_type == sample_type
        )
        if not exact_matches:
            return None
        if season is None:
            policy = _exactly_one(exact_matches)
            return policy if policy is not None and _has_no_season_window(policy) else None
        if not _is_season_or_none(season):
            return None
        season_specific = tuple(
            policy
            for policy in exact_matches
            if not _has_no_season_window(policy) and _season_contains(policy, season)
        )
        if season_specific:
            return _exactly_one(season_specific)
        unbounded = tuple(policy for policy in exact_matches if _has_no_season_window(policy))
        return _exactly_one(unbounded)


@dataclass(frozen=True)
class PolicyLoadResult:
    """The complete outcome of reading one local policy file."""

    policies: tuple[StabilizationPolicy, ...]
    load_status: PolicyLoadStatus
    error_messages: tuple[str, ...]
    source_path: str


def load_stabilization_policy_file(path: str | Path) -> PolicyLoadResult:
    """Load a complete JSON policy file, rejecting any malformed record."""

    source_path = str(path)
    policy_path = Path(path)
    if policy_path.suffix.casefold() != ".json":
        return _malformed_load(source_path, "policy file must use the .json extension")
    try:
        payload = json.loads(policy_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _malformed_load(source_path, "policy file is unreadable or invalid JSON")
    if not isinstance(payload, Mapping):
        return _malformed_load(source_path, "policy file top level must be an object")
    if set(payload) != _TOP_LEVEL_FIELDS:
        return _malformed_load(source_path, "policy file has missing or extra top-level fields")
    if not isinstance(payload["policy_file_version"], str) or not payload["policy_file_version"]:
        return _malformed_load(source_path, "policy_file_version must be a non-empty string")
    raw_policies = payload["policies"]
    if not isinstance(raw_policies, list) or not raw_policies:
        return _malformed_load(source_path, "policies must be a non-empty array")

    policies: list[StabilizationPolicy] = []
    errors: list[str] = []
    for index, raw_policy in enumerate(raw_policies):
        try:
            policies.append(_policy_from_mapping(raw_policy))
        except (TypeError, ValueError) as error:
            errors.append(f"policy at index {index}: {error}")
    if errors:
        return PolicyLoadResult((), "malformed", tuple(errors), source_path)
    try:
        StabilizationRegistry(policies)
    except (TypeError, ValueError) as error:
        return _malformed_load(source_path, str(error))
    return PolicyLoadResult(tuple(policies), "loaded", (), source_path)


def evaluate_stabilization(
    metric_key: str,
    sample_type: str | None,
    sample_n: int | float | None,
    registry: StabilizationRegistry,
    season: int | None = None,
) -> StabilizationDecision:
    """Evaluate only caller-provided sample metadata against a loaded policy."""

    if not isinstance(sample_type, str) or not sample_type:
        return _unverified_decision(metric_key, sample_type, sample_n, "sample_type is missing or invalid")
    if not _is_nonnegative_finite_number(sample_n):
        return _unverified_decision(metric_key, sample_type, sample_n, "sample_n is missing or invalid")
    policy = registry.resolve(metric_key, sample_type, season)
    if policy is None:
        return _unverified_decision(metric_key, sample_type, sample_n, "no unambiguous matching policy")
    status: StabilizationStatus = "pass" if sample_n >= policy.minimum_sample_value else "fail"
    reason = "sample meets policy minimum" if status == "pass" else "sample is below policy minimum"
    return _decision_from_policy(metric_key, sample_type, sample_n, policy, status, reason)


def apply_stabilization_to_observation(
    observation: MetricObservation,
    registry: StabilizationRegistry,
    season: int | None = None,
) -> MetricObservation:
    """Return a copied observation whose status reflects only this decision."""

    decision = evaluate_stabilization(
        observation.metric_key, observation.sample_type, observation.sample_n, registry, season
    )
    if decision.status in {"pass", "fail", "unverified"}:
        return replace(observation, stabilization_status=decision.status)
    return replace(observation)


def _policy_from_mapping(raw_policy: Any) -> StabilizationPolicy:
    if not isinstance(raw_policy, Mapping):
        raise TypeError("policy record must be an object")
    if set(raw_policy) != _POLICY_FIELDS:
        raise ValueError("policy record has missing or extra fields")
    return StabilizationPolicy(**dict(raw_policy))


def _malformed_load(source_path: str, error_message: str) -> PolicyLoadResult:
    return PolicyLoadResult((), "malformed", (error_message,), source_path)


def _unverified_decision(
    metric_key: str, sample_type: str | None, sample_n: int | float | None, reason: str
) -> StabilizationDecision:
    return StabilizationDecision(
        metric_key=metric_key,
        sample_type=sample_type,
        sample_n=sample_n,
        policy_id=None,
        policy_version=None,
        minimum_sample_ref=None,
        minimum_sample_value=None,
        source_name=None,
        source_url=None,
        retrieved_at_utc=None,
        status="unverified",
        reason=reason,
    )


def _decision_from_policy(
    metric_key: str,
    sample_type: str,
    sample_n: int | float,
    policy: StabilizationPolicy,
    status: StabilizationStatus,
    reason: str,
) -> StabilizationDecision:
    return StabilizationDecision(
        metric_key=metric_key,
        sample_type=sample_type,
        sample_n=sample_n,
        policy_id=policy.policy_id,
        policy_version=policy.policy_version,
        minimum_sample_ref=policy.minimum_sample_ref,
        minimum_sample_value=policy.minimum_sample_value,
        source_name=policy.source_name,
        source_url=policy.source_url,
        retrieved_at_utc=policy.retrieved_at_utc,
        status=status,
        reason=reason,
    )


def _is_positive_finite_number(value: Any) -> bool:
    """Validate a policy-provided minimum; ``float()`` is the zero boundary."""

    return _is_finite_number(value) and value > float()


def _is_nonnegative_finite_number(value: Any) -> bool:
    """Validate sample metadata; ``float()`` is the zero boundary."""

    return _is_finite_number(value) and value >= float()


def _is_finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value)


def _is_season_or_none(value: Any) -> bool:
    return value is None or isinstance(value, int) and not isinstance(value, bool)


def _has_no_season_window(policy: StabilizationPolicy) -> bool:
    return policy.applies_from_season is None and policy.applies_through_season is None


def _exactly_one(policies: Sequence[StabilizationPolicy]) -> StabilizationPolicy | None:
    try:
        policy, = policies
    except ValueError:
        return None
    return policy


def _season_contains(policy: StabilizationPolicy, season: int) -> bool:
    return (
        (policy.applies_from_season is None or policy.applies_from_season <= season)
        and (policy.applies_through_season is None or season <= policy.applies_through_season)
    )


def _is_source_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _is_utc_timestamp(value: str) -> bool:
    if not value.endswith(("Z", "+00:00")):
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True
