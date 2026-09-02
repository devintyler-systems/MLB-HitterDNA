"""Pure, fail-closed candidate filter-table evaluation with audit evidence."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from types import MappingProxyType
from typing import Any, Callable, Literal, Mapping, Sequence

from hitterdna.lineups import LineupPlayer, candidate_eligible_from_lineup
from hitterdna.statsapi import GameContext


MetricValue = float | int | str | None
ThresholdValue = float | int | str
FilterOperator = Literal["gte", "gt", "lte", "lt", "eq", "in", "custom"]
FilterStatus = Literal["PASS", "FAIL", "UNVERIFIED"]
StabilizationStatus = Literal["pass", "fail", "unverified", "not_applicable"]
CandidateDisposition = Literal["advance", "drop"]
CustomRule = Callable[["MetricObservation", "FilterDefinition", "ThresholdRegistry"], tuple[FilterStatus, str]]


@dataclass(frozen=True)
class MetricObservation:
    metric_key: str
    value: MetricValue
    sample_type: str
    sample_n: int | float | None
    source_url: str
    retrieved_at_utc: str
    source_name: str
    stabilization_status: StabilizationStatus
    notes: str | None


@dataclass(frozen=True)
class FilterDefinition:
    filter_id: str
    filter_version: str
    filter_name: str
    required: bool
    metric_key: str
    operator: FilterOperator
    threshold_ref: str | None
    allowed_values: tuple[str, ...] | None
    custom_rule_id: str | None
    description: str


@dataclass(frozen=True)
class ThresholdRegistry:
    values: Mapping[str, ThresholdValue]
    source_url: str
    retrieved_at_utc: str
    version: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "values", MappingProxyType(dict(self.values)))

    def resolve(self, threshold_ref: str | None) -> ThresholdValue | None:
        return self.values.get(threshold_ref) if threshold_ref else None


@dataclass(frozen=True)
class CandidateContext:
    analysis_date: str
    game_context: GameContext
    lineup_player: LineupPlayer
    opponent_abbreviation: str


@dataclass(frozen=True)
class FilterResult:
    analysis_date: str
    game_pk: int | None
    player_mlbam_id: int | None
    player_name: str
    team_abbreviation: str
    opponent_abbreviation: str
    batting_order: int | None
    pregame_eligibility: str
    filter_id: str
    filter_version: str
    filter_name: str
    required: bool
    metric_key: str
    actual_value: MetricValue
    sample_type: str
    sample_n: int | float | None
    metric_source_url: str | None
    metric_retrieved_at_utc: str | None
    stabilization_status: str
    threshold_ref: str | None
    threshold_value: ThresholdValue | None
    threshold_source_url: str | None
    threshold_retrieved_at_utc: str | None
    status: FilterStatus
    reason: str


@dataclass(frozen=True)
class CandidateFilterTable:
    analysis_date: str
    game_pk: int | None
    player_mlbam_id: int | None
    player_name: str
    candidate_disposition: CandidateDisposition
    results: tuple[FilterResult, ...]


def evaluate_filter(
    candidate_context: CandidateContext,
    definition: FilterDefinition,
    observations: Mapping[str, MetricObservation],
    thresholds: ThresholdRegistry,
    custom_rules: Mapping[str, CustomRule] | None = None,
) -> FilterResult:
    """Evaluate one declared filter without changing or inferring any input."""

    observation = observations.get(definition.metric_key)
    threshold_value = thresholds.resolve(definition.threshold_ref)
    result = _result_from_inputs(candidate_context, definition, observation, thresholds, threshold_value)

    definition_problem = _definition_problem(definition)
    if definition_problem:
        return _replace_result(result, status="UNVERIFIED", reason=definition_problem)
    observation_problem = _observation_problem(observation, definition.metric_key)
    if observation_problem:
        return _replace_result(result, status="UNVERIFIED", reason=observation_problem)
    assert observation is not None

    if definition.operator == "custom":
        return _evaluate_custom_rule(result, observation, definition, thresholds, custom_rules)
    if definition.operator == "in":
        return _evaluate_allowed_values(result, observation, definition)
    threshold_problem = _threshold_problem(thresholds)
    if threshold_problem:
        return _replace_result(result, status="UNVERIFIED", reason=threshold_problem)
    if threshold_value is None:
        return _replace_result(result, status="UNVERIFIED", reason="threshold reference not found")
    return _evaluate_comparison(result, observation.value, threshold_value, definition.operator)


def build_candidate_filter_table(
    analysis_date: str,
    game_context: GameContext,
    lineup_player: LineupPlayer,
    opponent_abbreviation: str,
    definitions: Sequence[FilterDefinition],
    observations: Mapping[str, MetricObservation],
    thresholds: ThresholdRegistry,
    custom_rules: Mapping[str, CustomRule] | None = None,
) -> CandidateFilterTable:
    """Build one immutable, ordered audit table for a candidate."""

    candidate_context = CandidateContext(
        analysis_date=analysis_date,
        game_context=game_context,
        lineup_player=lineup_player,
        opponent_abbreviation=opponent_abbreviation,
    )
    intake_problem = _intake_problem(candidate_context)
    if intake_problem:
        result = _intake_result(candidate_context, intake_problem)
        return _table_from_results(candidate_context, (result,))
    if not definitions:
        result = _intake_result(candidate_context, "no filter definitions supplied")
        return _table_from_results(candidate_context, (result,))

    results = tuple(
        evaluate_filter(candidate_context, definition, observations, thresholds, custom_rules)
        for definition in definitions
    )
    return _table_from_results(candidate_context, results)


def serialize_filter_table(table: CandidateFilterTable) -> dict[str, Any]:
    """Serialize an immutable table using JSON-native values in stable order."""

    return {
        "analysis_date": table.analysis_date,
        "game_pk": table.game_pk,
        "player_mlbam_id": table.player_mlbam_id,
        "player_name": table.player_name,
        "candidate_disposition": table.candidate_disposition,
        "results": [asdict(result) for result in table.results],
    }


def candidate_can_advance(table: CandidateFilterTable) -> bool:
    """Return whether the table has no failed or unverified required result."""

    return (
        table.candidate_disposition == "advance"
        and bool(table.results)
        and all(not result.required or result.status == "PASS" for result in table.results)
    )


def _result_from_inputs(
    candidate_context: CandidateContext,
    definition: FilterDefinition,
    observation: MetricObservation | None,
    thresholds: ThresholdRegistry,
    threshold_value: ThresholdValue | None,
) -> FilterResult:
    player = candidate_context.lineup_player
    return FilterResult(
        analysis_date=candidate_context.analysis_date,
        game_pk=candidate_context.game_context.game_pk,
        player_mlbam_id=player.player_mlbam_id,
        player_name=player.player_name,
        team_abbreviation=player.team_abbreviation,
        opponent_abbreviation=candidate_context.opponent_abbreviation,
        batting_order=player.batting_order,
        pregame_eligibility=candidate_context.game_context.pregame_eligibility,
        filter_id=definition.filter_id,
        filter_version=definition.filter_version,
        filter_name=definition.filter_name,
        required=definition.required,
        metric_key=definition.metric_key,
        actual_value=observation.value if observation else None,
        sample_type=observation.sample_type if observation else "",
        sample_n=observation.sample_n if observation else None,
        metric_source_url=observation.source_url if observation else None,
        metric_retrieved_at_utc=observation.retrieved_at_utc if observation else None,
        stabilization_status=observation.stabilization_status if observation else "unverified",
        threshold_ref=definition.threshold_ref,
        threshold_value=threshold_value,
        threshold_source_url=thresholds.source_url if definition.threshold_ref else None,
        threshold_retrieved_at_utc=thresholds.retrieved_at_utc if definition.threshold_ref else None,
        status="UNVERIFIED",
        reason="not evaluated",
    )


def _replace_result(result: FilterResult, *, status: FilterStatus, reason: str) -> FilterResult:
    return replace(result, status=status, reason=reason)


def _definition_problem(definition: FilterDefinition) -> str | None:
    if definition.operator in {"gte", "gt", "lte", "lt", "eq"} and not definition.threshold_ref:
        return "numeric comparison requires threshold_ref"
    if definition.operator == "in" and not definition.allowed_values:
        return "in comparison requires allowed_values"
    if definition.operator == "custom" and not definition.custom_rule_id:
        return "custom comparison requires custom_rule_id"
    if definition.operator not in {"gte", "gt", "lte", "lt", "eq", "in", "custom"}:
        return "unsupported operator"
    return None


def _observation_problem(observation: MetricObservation | None, metric_key: str) -> str | None:
    if observation is None:
        return "missing metric observation"
    if observation.metric_key != metric_key:
        return "metric key does not match filter definition"
    if not observation.source_url:
        return "metric observation missing source_url"
    if not observation.retrieved_at_utc:
        return "metric observation missing retrieved_at_utc"
    if not observation.sample_type:
        return "metric observation missing sample_type"
    if observation.sample_n is None:
        return "metric observation missing sample_n"
    if not _is_sample_size(observation.sample_n):
        return "metric observation has invalid sample_n"
    if observation.stabilization_status in {"fail", "unverified"}:
        return "metric observation is not stabilized"
    if observation.stabilization_status not in {"pass", "fail", "unverified", "not_applicable"}:
        return "metric observation has invalid stabilization_status"
    return None


def _threshold_problem(thresholds: ThresholdRegistry) -> str | None:
    if not thresholds.source_url:
        return "threshold registry missing source_url"
    if not thresholds.retrieved_at_utc:
        return "threshold registry missing retrieved_at_utc"
    if not thresholds.version:
        return "threshold registry missing version"
    return None


def _evaluate_comparison(
    result: FilterResult,
    actual_value: MetricValue,
    threshold_value: ThresholdValue,
    operator: FilterOperator,
) -> FilterResult:
    if operator == "eq":
        if not _equality_values_are_valid(actual_value, threshold_value):
            return _replace_result(result, status="UNVERIFIED", reason="comparison type error")
        return _comparison_result(result, actual_value == threshold_value)
    if not _is_numeric(actual_value) or not _is_numeric(threshold_value):
        return _replace_result(result, status="UNVERIFIED", reason="comparison type error")
    try:
        if operator == "gte":
            outcome = actual_value >= threshold_value
        elif operator == "gt":
            outcome = actual_value > threshold_value
        elif operator == "lte":
            outcome = actual_value <= threshold_value
        elif operator == "lt":
            outcome = actual_value < threshold_value
        else:
            return _replace_result(result, status="UNVERIFIED", reason="unsupported operator")
    except (TypeError, ValueError):
        return _replace_result(result, status="UNVERIFIED", reason="comparison error")
    return _comparison_result(result, outcome)


def _evaluate_allowed_values(
    result: FilterResult,
    observation: MetricObservation,
    definition: FilterDefinition,
) -> FilterResult:
    if not isinstance(observation.value, str) or definition.allowed_values is None:
        return _replace_result(result, status="UNVERIFIED", reason="comparison type error")
    return _comparison_result(result, observation.value in definition.allowed_values)


def _evaluate_custom_rule(
    result: FilterResult,
    observation: MetricObservation,
    definition: FilterDefinition,
    thresholds: ThresholdRegistry,
    custom_rules: Mapping[str, CustomRule] | None,
) -> FilterResult:
    rule = custom_rules.get(definition.custom_rule_id) if custom_rules and definition.custom_rule_id else None
    if rule is None:
        return _replace_result(result, status="UNVERIFIED", reason="custom rule not found")
    try:
        status, reason = rule(observation, definition, thresholds)
    except Exception:
        return _replace_result(result, status="UNVERIFIED", reason="custom rule exception")
    if status not in {"PASS", "FAIL", "UNVERIFIED"} or not isinstance(reason, str):
        return _replace_result(result, status="UNVERIFIED", reason="custom rule returned invalid result")
    return _replace_result(result, status=status, reason=reason)


def _comparison_result(result: FilterResult, outcome: bool) -> FilterResult:
    return _replace_result(
        result,
        status="PASS" if outcome else "FAIL",
        reason="comparison passed" if outcome else "comparison failed",
    )


def _intake_problem(candidate_context: CandidateContext) -> str | None:
    game_context = candidate_context.game_context
    player = candidate_context.lineup_player
    if not _is_positive_integer(game_context.game_pk):
        return "game_context game_pk is not a positive integer"
    if game_context.pregame_eligibility not in {"eligible_refresh", "urgent_refresh"}:
        return "game_context is not pregame refresh eligible"
    if player.game_pk != game_context.game_pk:
        return "lineup player game_pk does not match game_context"
    if not candidate_eligible_from_lineup(player):
        return "lineup player is not confirmed and candidate eligible"
    return None


def _intake_result(candidate_context: CandidateContext, reason: str) -> FilterResult:
    player = candidate_context.lineup_player
    return FilterResult(
        analysis_date=candidate_context.analysis_date,
        game_pk=candidate_context.game_context.game_pk,
        player_mlbam_id=player.player_mlbam_id,
        player_name=player.player_name,
        team_abbreviation=player.team_abbreviation,
        opponent_abbreviation=candidate_context.opponent_abbreviation,
        batting_order=player.batting_order,
        pregame_eligibility=candidate_context.game_context.pregame_eligibility,
        filter_id="intake-eligibility",
        filter_version="intake",
        filter_name="Candidate intake eligibility",
        required=True,
        metric_key="candidate_intake",
        actual_value=None,
        sample_type="",
        sample_n=None,
        metric_source_url=None,
        metric_retrieved_at_utc=None,
        stabilization_status="not_applicable",
        threshold_ref=None,
        threshold_value=None,
        threshold_source_url=None,
        threshold_retrieved_at_utc=None,
        status="FAIL" if reason != "no filter definitions supplied" else "UNVERIFIED",
        reason=reason,
    )


def _table_from_results(
    candidate_context: CandidateContext, results: tuple[FilterResult, ...]
) -> CandidateFilterTable:
    player = candidate_context.lineup_player
    candidate_disposition: CandidateDisposition = (
        "advance" if all(not result.required or result.status == "PASS" for result in results) else "drop"
    )
    return CandidateFilterTable(
        analysis_date=candidate_context.analysis_date,
        game_pk=candidate_context.game_context.game_pk,
        player_mlbam_id=player.player_mlbam_id,
        player_name=player.player_name,
        candidate_disposition=candidate_disposition,
        results=results,
    )


def _is_numeric(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _equality_values_are_valid(actual_value: MetricValue, threshold_value: ThresholdValue) -> bool:
    if _is_numeric(actual_value) and _is_numeric(threshold_value):
        return True
    return isinstance(actual_value, str) and isinstance(threshold_value, str)


def _is_sample_size(value: Any) -> bool:
    return _is_numeric(value)


def _is_positive_integer(value: Any) -> bool:
    """Identity validation only; this is not a filter threshold."""

    return isinstance(value, int) and not isinstance(value, bool) and value >= 1
