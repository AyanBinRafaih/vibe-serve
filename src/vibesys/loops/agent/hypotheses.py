"""Pure hypothesis-ledger projection and transition functions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from vibesys.loops.agent.model import (
    ActiveHypothesis,
    HypothesisLedger,
    HypothesisMeasurement,
    HypothesisRecord,
    HypothesisResolution,
    HypothesisReview,
    HypothesisStrategyState,
)
from vibesys.schemas import HypothesisOutcome, HypothesisStrategyUpdate

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from vs_loop_state import RoundRecord


@dataclass(frozen=True)
class ResolutionEvidence:
    """Inputs the framework needs to finalize one hypothesis declaration."""

    declared: HypothesisOutcome | None
    passed: bool
    reviewed: bool
    official_metric: float | None
    baseline_metric: float | None
    direction: Literal["max", "min"] | None
    noise_fraction: float = 0.0
    benchmark_expected: bool = False


def resolve_hypothesis_outcome(
    evidence: ResolutionEvidence,
) -> HypothesisResolution | None:
    """Resolve one declaration only after review and trusted evidence."""
    if not evidence.reviewed:
        resolution = None
    elif not evidence.passed:
        resolution = HypothesisResolution.REJECTED
    elif evidence.declared is None:
        resolution = None
    else:
        resolution = {
            HypothesisOutcome.DISPROVEN: HypothesisResolution.DISPROVEN,
            HypothesisOutcome.IMPLEMENTATION_FAILED: (HypothesisResolution.IMPLEMENTATION_FAILED),
            HypothesisOutcome.INCONCLUSIVE: HypothesisResolution.INCONCLUSIVE,
            HypothesisOutcome.BLOCKED: HypothesisResolution.BLOCKED,
        }.get(evidence.declared)
        if evidence.declared is HypothesisOutcome.CONTINUE:
            resolution = None
        elif resolution is None:
            if not evidence.benchmark_expected:
                # Without a benchmark contract, incidental metrics have no
                # lifecycle meaning. Independent review resolves the claim.
                resolution = HypothesisResolution.PROVEN
            elif evidence.official_metric is not None:
                resolution = _resolve_metric_evidence(evidence)
            elif evidence.declared is HypothesisOutcome.SUPPORTED:
                resolution = HypothesisResolution.PROVEN
            else:
                resolution = HypothesisResolution.INCONCLUSIVE
    return resolution


def _resolve_metric_evidence(evidence: ResolutionEvidence) -> HypothesisResolution:
    """Classify a reviewed nomination from its official parent comparison."""
    if (
        evidence.official_metric is None
        or evidence.baseline_metric in {None, 0}
        or evidence.direction not in {"max", "min"}
    ):
        return HypothesisResolution.INCONCLUSIVE
    baseline = evidence.baseline_metric
    assert baseline is not None  # noqa: S101  # narrowed by the guard above
    raw_delta = (evidence.official_metric - baseline) / abs(baseline) * 100
    benefit = raw_delta if evidence.direction == "max" else -raw_delta
    tolerance_pct = evidence.noise_fraction * 100
    if benefit < -tolerance_pct:
        return HypothesisResolution.DISPROVEN
    if benefit > tolerance_pct:
        return HypothesisResolution.PROVEN
    return HypothesisResolution.INCONCLUSIVE


def scalar_candidate_retained(
    *,
    metric: float | None,
    direction: Literal["max", "min"] | None,
    prior: Sequence[float],
    noise_fraction: float = 0.0,
) -> bool | None:
    """Return whether an official scalar candidate advances the best checkpoint."""
    if metric is None or direction not in {"max", "min"}:
        return None
    if not prior:
        return True
    best = max(prior) if direction == "max" else min(prior)
    tolerance = abs(best) * noise_fraction
    return metric > best + tolerance if direction == "max" else metric < best - tolerance


def metric_baseline(
    *,
    parent_round: int | None,
    parent_commit: str | None,
    metric: str | None,
    rounds: Sequence[RoundRecord],
) -> RoundRecord | None:
    """Resolve the measured causal parent, preferring exact commit provenance."""
    comparable = [
        item
        for item in rounds
        if item.official_evaluation and item.perf_metric is not None and item.perf_unit == metric
    ]
    if parent_commit is not None:
        match = next(
            (item for item in reversed(comparable) if item.commit == parent_commit),
            None,
        )
        if match is not None:
            return match
        # A commit pin is stronger provenance than the legacy round number.  A
        # missing pin means the causal baseline is unknown, not that another
        # same-numbered measurement is interchangeable.
        return None
    if parent_round is not None:
        return next(
            (item for item in reversed(comparable) if item.round_number == parent_round),
            None,
        )
    return comparable[-1] if comparable else None


def reconcile_hypothesis_ledger(
    existing: HypothesisLedger | None,
    rounds: Sequence[RoundRecord],
    active: ActiveHypothesis | None,
    *,
    legacy_directions: Mapping[str, Literal["max", "min"]] | None = None,
) -> HypothesisLedger:
    """Project evidence, using configured direction only for legacy records."""
    strategy = {
        item.hypothesis_id: (item.strategy, item.strategy_reason)
        for item in (existing.hypotheses if existing is not None else ())
        if item.strategy
        in {
            HypothesisStrategyState.PARKED,
            HypothesisStrategyState.ABANDONED,
        }
    }
    saved_active = (
        next(
            (
                item.model_copy(deep=True)
                for item in existing.hypotheses
                if item.strategy is HypothesisStrategyState.ACTIVE
            ),
            None,
        )
        if existing is not None and active is None
        else None
    )
    ledger = HypothesisLedger()
    prior_rounds: list[RoundRecord] = []
    for round_record in rounds:
        _apply_round(ledger, round_record, prior_rounds, legacy_directions)
        prior_rounds.append(round_record)
    for item in ledger.hypotheses:
        saved = strategy.get(item.hypothesis_id)
        if saved is not None and item.strategy is not HypothesisStrategyState.ACTIVE:
            item.strategy, item.strategy_reason = saved
    if active is not None:
        # The active plan is already the durable handoff if interruption lands
        # between the local checkpoint and portable-ledger writes. Replay its
        # validated strategy updates to make that start transition recoverable.
        ledger = apply_strategy_updates(ledger, active.plan.hypothesis_updates)
        _ensure_active(ledger, active)
    elif saved_active is not None:
        # Preserve a portable ACTIVE decision only when that ledger had already
        # observed every reconstructed round. Newer immutable round evidence
        # supersedes a stale active snapshot after a terminal transition.
        reconstructed = ledger.by_id(saved_active.hypothesis_id)
        if reconstructed is None:
            ledger.hypotheses.append(saved_active)
        elif set(reconstructed.rounds).issubset(saved_active.rounds):
            # The portable record was written after every reconstructed round,
            # so its ACTIVE transition is newer than the evidence projection.
            reconstructed.strategy = HypothesisStrategyState.ACTIVE
            reconstructed.strategy_reason = None
    return _validated_ledger(ledger)


def apply_strategy_updates(
    ledger: HypothesisLedger,
    updates: Sequence[HypothesisStrategyUpdate],
) -> HypothesisLedger:
    """Apply validated orchestrator-owned parked/abandoned transitions."""
    updated = ledger.model_copy(deep=True)
    seen: set[str] = set()
    for change in updates:
        if change.hypothesis_id in seen:
            raise ValueError(  # noqa: TRY003  # tracked: #288
                f"duplicate strategy update for hypothesis {change.hypothesis_id!r}"
            )
        seen.add(change.hypothesis_id)
        item = updated.by_id(change.hypothesis_id)
        if item is None:
            raise ValueError(  # noqa: TRY003  # tracked: #288
                f"strategy update names unknown hypothesis {change.hypothesis_id!r}"
            )
        if item.strategy is HypothesisStrategyState.ACTIVE:
            raise ValueError(  # noqa: TRY003  # tracked: #288
                f"cannot {change.disposition} active hypothesis {change.hypothesis_id!r}"
            )
        item.strategy = HypothesisStrategyState(change.disposition)
        item.strategy_reason = change.reason.strip()
    return _validated_ledger(updated)


def start_hypothesis(
    ledger: HypothesisLedger,
    active: ActiveHypothesis,
) -> HypothesisLedger:
    """Add the framework-created active hypothesis if it is not present."""
    updated = ledger.model_copy(deep=True)
    existing = updated.by_id(active.plan.hypothesis_id)
    if existing is not None:
        if existing.strategy is not HypothesisStrategyState.ACTIVE:
            raise ValueError(  # noqa: TRY003  # tracked: #288
                f"hypothesis ID {active.plan.hypothesis_id!r} was already completed"
            )
        return _validated_ledger(updated)
    updated.hypotheses.append(_active_record(active))
    return _validated_ledger(updated)


def _validated_ledger(ledger: HypothesisLedger) -> HypothesisLedger:
    return HypothesisLedger.model_validate(ledger.model_dump())


def _ensure_active(ledger: HypothesisLedger, active: ActiveHypothesis) -> None:
    item = ledger.by_id(active.plan.hypothesis_id)
    if item is None:
        ledger.hypotheses.append(_active_record(active))
        return
    item.strategy = HypothesisStrategyState.ACTIVE
    item.strategy_reason = None


def _active_record(active: ActiveHypothesis) -> HypothesisRecord:
    return HypothesisRecord(
        hypothesis_id=active.plan.hypothesis_id,
        claim=active.plan.hypothesis or None,
        task=active.plan.task or None,
        started_round=active.started_round,
        parent_round=active.parent_round,
        parent_commit=active.parent_commit,
    )


def _apply_round(
    ledger: HypothesisLedger,
    record: RoundRecord,
    prior_rounds: Sequence[RoundRecord],
    legacy_directions: Mapping[str, Literal["max", "min"]] | None,
) -> None:
    identifier = record.hypothesis_id
    if not identifier:
        return
    item = ledger.by_id(identifier)
    if item is None:
        item = HypothesisRecord(
            hypothesis_id=identifier,
            claim=record.hypothesis_claim,
            task=record.hypothesis_task,
            started_round=record.round_number,
            parent_round=record.hypothesis_parent_round,
            parent_commit=record.hypothesis_parent_commit,
        )
        ledger.hypotheses.append(item)
    if record.round_number not in item.rounds:
        item.rounds.append(record.round_number)
    item.claim = record.hypothesis_claim or item.claim
    item.task = record.hypothesis_task or item.task
    item.parent_round = record.hypothesis_parent_round
    item.parent_commit = record.hypothesis_parent_commit
    item.declared_outcome = _declared_outcome(record.hypothesis_declared_outcome)
    item.review = _review(record)
    item.resolution = (
        _resolution(record.hypothesis_outcome)
        if item.review not in {HypothesisReview.PENDING, HypothesisReview.DEFERRED}
        else None
    )
    measurement = _measurement(record, prior_rounds, legacy_directions)
    if measurement is not None:
        item.measurement = measurement
    retained = _retained(record, prior_rounds, legacy_directions)
    if retained is not None:
        item.candidate_retained = retained
    item.strategy = HypothesisStrategyState.COMPLETED
    _correct_legacy_resolution(item, record, measurement)


def _correct_legacy_resolution(
    item: HypothesisRecord,
    record: RoundRecord,
    measurement: HypothesisMeasurement | None,
) -> None:
    """Repair old pass-derived resolutions only when direction is known."""
    if (
        record.hypothesis_declared_outcome is None
        and record.candidate_retained is None
        and item.resolution is HypothesisResolution.PROVEN
    ):
        if (
            record.official_evaluation
            and record.perf_metric is not None
            and (measurement is None or measurement.delta_pct is None)
        ):
            # A legacy "proven" performance claim needs both configured
            # direction and a causal baseline before it has lifecycle meaning.
            item.resolution = HypothesisResolution.INCONCLUSIVE
        elif measurement is not None:
            assert measurement.delta_pct is not None  # noqa: S101  # branch above
            benefit = measurement.delta_pct
            if measurement.direction == "min":
                benefit = -benefit
            if benefit < 0:
                item.resolution = HypothesisResolution.DISPROVEN
            elif benefit == 0:
                item.resolution = HypothesisResolution.INCONCLUSIVE


def _declared_outcome(value: str | None) -> HypothesisOutcome | None:
    if value is None:
        return None
    try:
        return HypothesisOutcome(value)
    except ValueError:
        return None


def _review(record: RoundRecord) -> HypothesisReview:
    if record.judge_verdict is not None:
        return HypothesisReview(record.judge_verdict)
    if not record.reviewed:
        return HypothesisReview.DEFERRED
    return HypothesisReview.PASS if record.passed else HypothesisReview.FAIL


def _resolution(value: str | None) -> HypothesisResolution | None:
    if value is None or value == HypothesisOutcome.CONTINUE.value:
        return None
    if value in {HypothesisOutcome.SUPPORTED.value, HypothesisOutcome.NOMINATED.value}:
        return None
    try:
        return HypothesisResolution(value)
    except ValueError:
        return HypothesisResolution.INCONCLUSIVE


def _measurement(
    record: RoundRecord,
    prior_rounds: Sequence[RoundRecord],
    legacy_directions: Mapping[str, Literal["max", "min"]] | None,
) -> HypothesisMeasurement | None:
    if not record.official_evaluation or record.perf_metric is None or record.perf_unit is None:
        return None
    direction = record.perf_direction or _configured_legacy_direction(record, legacy_directions)
    if direction not in {"max", "min"}:
        # Keep a legacy raw measurement in the round journal when the run's
        # configuration does not establish its objective direction.
        return None
    baseline = _baseline(record, prior_rounds)
    baseline_value = record.perf_baseline_metric
    if baseline_value is None and baseline is not None:
        baseline_value = baseline.perf_metric
    delta = record.perf_delta_pct
    if delta is None and baseline_value not in {None, 0}:
        baseline_metric = baseline_value
        assert baseline_metric is not None  # noqa: S101  # narrowed by the guard above
        delta = (record.perf_metric - baseline_metric) / abs(baseline_metric) * 100
    return HypothesisMeasurement(
        round=record.round_number,
        metric=record.perf_unit,
        value=record.perf_metric,
        unit=record.perf_unit,
        direction=direction,
        baseline_round=(
            record.perf_baseline_round
            if record.perf_baseline_round is not None
            else baseline.round_number
            if baseline is not None
            else None
        ),
        baseline_commit=record.perf_baseline_commit or record.hypothesis_parent_commit,
        baseline_value=baseline_value,
        delta_pct=delta,
    )


def _baseline(
    record: RoundRecord,
    prior_rounds: Sequence[RoundRecord],
) -> RoundRecord | None:
    comparable = [
        prior
        for prior in prior_rounds
        if prior.official_evaluation
        and prior.perf_metric is not None
        and prior.perf_unit == record.perf_unit
    ]
    if record.hypothesis_parent_commit is not None:
        match = next(
            (
                prior
                for prior in reversed(comparable)
                if prior.commit == record.hypothesis_parent_commit
            ),
            None,
        )
        if match is not None:
            return match
        return None
    if record.hypothesis_parent_round is not None:
        return next(
            (
                prior
                for prior in reversed(comparable)
                if prior.round_number == record.hypothesis_parent_round
            ),
            None,
        )
    return comparable[-1] if comparable else None


def _retained(
    record: RoundRecord,
    prior_rounds: Sequence[RoundRecord],
    legacy_directions: Mapping[str, Literal["max", "min"]] | None,
) -> bool | None:
    if record.candidate_retained is not None:
        retained = record.candidate_retained
    elif record.judge_verdict is not None:
        # Typed records deliberately distinguish unknown from false. Raw
        # implementer dispositions are evidence, not a retention decision.
        retained = None
    elif record.candidate_disposition in {"pareto_frontier", "prerequisite"}:
        retained = True
    elif record.candidate_disposition == "discard":
        retained = False
    elif not record.official_evaluation or record.perf_metric is None:
        retained = None
    else:
        direction = record.perf_direction or _configured_legacy_direction(record, legacy_directions)
        if direction not in {"max", "min"}:
            return None
        comparable = [
            prior.perf_metric
            for prior in prior_rounds
            if prior.official_evaluation
            and prior.passed
            and prior.perf_metric is not None
            and prior.perf_unit == record.perf_unit
        ]
        if not comparable:
            retained = True
        elif direction == "min":
            retained = record.perf_metric < min(comparable)
        else:
            retained = record.perf_metric > max(comparable)
    return retained


def _configured_legacy_direction(
    record: RoundRecord,
    directions: Mapping[str, Literal["max", "min"]] | None,
) -> Literal["max", "min"] | None:
    if record.judge_verdict is not None or record.perf_unit is None or directions is None:
        return None
    return directions.get(record.perf_unit)
