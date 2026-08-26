"""Hypothesis lifecycle projection and evidence-resolution regressions."""

from typing import Literal

import pytest

from vibesys.loops.agent.hypotheses import (
    ResolutionEvidence,
    apply_strategy_updates,
    metric_baseline,
    reconcile_hypothesis_ledger,
    resolve_hypothesis_outcome,
    scalar_candidate_retained,
)
from vibesys.loops.agent.model import (
    ActiveHypothesis,
    HypothesisLedger,
    HypothesisRecord,
    HypothesisResolution,
    HypothesisStrategyState,
)
from vibesys.schemas import (
    HypothesisOutcome,
    HypothesisStrategyUpdate,
    OrchestratorPlan,
)
from vs_loop_state import RoundRecord


def _official_round(  # noqa: PLR0913  # test fixture makes evidence explicit
    number: int,
    metric: float,
    *,
    hypothesis_id: str,
    parent_round: int | None = None,
    parent_commit: str | None = None,
    outcome: str = "proven",
    declared: str | None = None,
    direction: Literal["max", "min"] = "max",
    candidate_retained: bool | None = True,
) -> RoundRecord:
    return RoundRecord(
        round_number=number,
        commit=f"{number:040x}",
        perf_metric=metric,
        perf_unit="total_ops_per_sec",
        passed=True,
        hypothesis_id=hypothesis_id,
        hypothesis_declared_outcome=declared,
        judge_verdict="pass",
        hypothesis_outcome=outcome,
        hypothesis_parent_round=parent_round,
        hypothesis_parent_commit=parent_commit,
        metrics={"total_ops_per_sec": metric},
        official_evaluation=True,
        perf_direction=direction,
        candidate_retained=candidate_retained,
    )


def test_queue_rs_regression_is_disproven_and_not_retained() -> None:
    """Configured legacy M3 is repaired from evidence, not its old pass mapping."""
    rounds = [
        RoundRecord(
            round_number=2,
            commit=f"{2:040x}",
            perf_metric=104_257_741.0,
            perf_unit="total_ops_per_sec",
            passed=True,
            reviewed=True,
            hypothesis_id="m2-preallocated-spsc-ring",
            hypothesis_outcome="proven",
            official_evaluation=True,
        ),
        RoundRecord(
            round_number=3,
            commit=f"{3:040x}",
            perf_metric=97_028_091.721612,
            perf_unit="total_ops_per_sec",
            passed=True,
            reviewed=True,
            hypothesis_id="m3-pow2-mask-addressing",
            hypothesis_outcome="proven",
            hypothesis_parent_round=2,
            official_evaluation=True,
        ),
    ]

    m3 = reconcile_hypothesis_ledger(
        None,
        rounds,
        None,
        legacy_directions={"total_ops_per_sec": "max"},
    ).by_id("m3-pow2-mask-addressing")

    assert m3 is not None
    assert m3.resolution is HypothesisResolution.DISPROVEN
    assert m3.candidate_retained is False
    assert m3.measurement is not None
    assert m3.measurement.baseline_round == 2
    assert m3.measurement.delta_pct is not None
    assert m3.measurement.delta_pct < 0


def test_configured_legacy_minimize_run_uses_min_direction() -> None:
    rounds = [
        RoundRecord(
            1,
            "a" * 40,
            100.0,
            "latency_ms",
            True,  # noqa: FBT003  # legacy positional codec shape
            hypothesis_id="parent",
        ),
        RoundRecord(
            2,
            "b" * 40,
            90.0,
            "latency_ms",
            True,  # noqa: FBT003  # tracked: #288
            hypothesis_id="child",
            hypothesis_outcome="proven",
            hypothesis_parent_round=1,
            official_evaluation=True,
        ),
    ]
    rounds[0].official_evaluation = True

    child = reconcile_hypothesis_ledger(
        None,
        rounds,
        None,
        legacy_directions={"latency_ms": "min"},
    ).by_id("child")

    assert child is not None
    assert child.resolution is HypothesisResolution.PROVEN
    assert child.candidate_retained is True
    assert child.measurement is not None
    assert child.measurement.direction == "min"


def test_configured_legacy_metric_without_causal_baseline_is_inconclusive() -> None:
    legacy = RoundRecord(
        round_number=1,
        commit="a" * 40,
        perf_metric=100.0,
        perf_unit="ops",
        passed=True,
        reviewed=True,
        hypothesis_id="H-1",
        hypothesis_outcome="proven",
        official_evaluation=True,
    )

    hypothesis = reconcile_hypothesis_ledger(
        None,
        [legacy],
        None,
        legacy_directions={"ops": "max"},
    ).by_id("H-1")

    assert hypothesis is not None
    assert hypothesis.resolution is HypothesisResolution.INCONCLUSIVE


def test_projection_uses_hypothesis_parent_not_previous_round() -> None:
    rounds = [
        _official_round(2, 100.0, hypothesis_id="parent"),
        _official_round(3, 70.0, hypothesis_id="unrelated"),
        _official_round(
            4,
            90.0,
            hypothesis_id="child",
            parent_round=2,
            candidate_retained=None,
        ),
    ]

    child = reconcile_hypothesis_ledger(None, rounds, None).by_id("child")

    assert child is not None
    assert child.resolution is HypothesisResolution.DISPROVEN
    assert child.measurement is not None
    assert child.measurement.baseline_round == 2
    assert child.measurement.delta_pct == -10.0


def test_projection_preserves_latest_official_measurement_across_provisional_rounds() -> None:
    measured = _official_round(1, 100.0, hypothesis_id="H-1")
    provisional = RoundRecord(
        round_number=2,
        commit="c2",
        perf_metric=None,
        perf_unit=None,
        passed=False,
        reviewed=False,
        hypothesis_id="H-1",
        hypothesis_declared_outcome="continue",
        hypothesis_outcome="continue",
    )

    hypothesis = reconcile_hypothesis_ledger(None, [measured, provisional], None).by_id("H-1")

    assert hypothesis is not None
    assert hypothesis.measurement is not None
    assert hypothesis.measurement.value == 100.0
    assert hypothesis.candidate_retained is True


def test_metric_baseline_prefers_exact_parent_commit_over_round_number() -> None:
    parent = _official_round(1, 100.0, hypothesis_id="parent")
    later = _official_round(2, 120.0, hypothesis_id="later")

    baseline = metric_baseline(
        parent_round=2,
        parent_commit=parent.commit,
        metric="total_ops_per_sec",
        rounds=[parent, later],
    )

    assert baseline is parent


def test_metric_baseline_fails_closed_when_exact_parent_commit_is_missing() -> None:
    parent = _official_round(1, 100.0, hypothesis_id="parent")
    later = _official_round(2, 120.0, hypothesis_id="later")

    baseline = metric_baseline(
        parent_round=2,
        parent_commit="missing-parent-commit",
        metric="total_ops_per_sec",
        rounds=[parent, later],
    )

    assert baseline is None


def test_framework_resolves_max_and_min_objectives_directionally() -> None:
    maximize = resolve_hypothesis_outcome(
        ResolutionEvidence(
            declared=HypothesisOutcome.NOMINATED,
            passed=True,
            reviewed=True,
            official_metric=90.0,
            baseline_metric=100.0,
            direction="max",
            benchmark_expected=True,
        )
    )
    minimize = resolve_hypothesis_outcome(
        ResolutionEvidence(
            declared=HypothesisOutcome.NOMINATED,
            passed=True,
            reviewed=True,
            official_metric=90.0,
            baseline_metric=100.0,
            direction="min",
            benchmark_expected=True,
        )
    )

    assert maximize is HypothesisResolution.DISPROVEN
    assert minimize is HypothesisResolution.PROVEN


def test_official_regression_overrides_implementer_support() -> None:
    resolution = resolve_hypothesis_outcome(
        ResolutionEvidence(
            declared=HypothesisOutcome.SUPPORTED,
            passed=True,
            reviewed=True,
            official_metric=90.0,
            baseline_metric=100.0,
            direction="max",
            benchmark_expected=True,
        )
    )

    assert resolution is HypothesisResolution.DISPROVEN


@pytest.mark.parametrize(
    "declared",
    [HypothesisOutcome.SUPPORTED, HypothesisOutcome.NOMINATED],
)
def test_non_benchmark_claim_ignores_incidental_official_metric(
    declared: HypothesisOutcome,
) -> None:
    resolution = resolve_hypothesis_outcome(
        ResolutionEvidence(
            declared=declared,
            passed=True,
            reviewed=True,
            official_metric=90.0,
            baseline_metric=100.0,
            direction="max",
            benchmark_expected=False,
        )
    )

    assert resolution is HypothesisResolution.PROVEN


def test_noise_band_and_missing_parent_are_inconclusive() -> None:
    within_noise = resolve_hypothesis_outcome(
        ResolutionEvidence(
            declared=HypothesisOutcome.NOMINATED,
            passed=True,
            reviewed=True,
            official_metric=100.5,
            baseline_metric=100.0,
            direction="max",
            noise_fraction=0.01,
            benchmark_expected=True,
        )
    )
    missing_parent = resolve_hypothesis_outcome(
        ResolutionEvidence(
            declared=HypothesisOutcome.NOMINATED,
            passed=True,
            reviewed=True,
            official_metric=110.0,
            baseline_metric=None,
            direction="max",
            benchmark_expected=True,
        )
    )

    assert within_noise is HypothesisResolution.INCONCLUSIVE
    assert missing_parent is HypothesisResolution.INCONCLUSIVE


def test_scalar_retention_requires_a_directional_new_best() -> None:
    assert (
        scalar_candidate_retained(
            metric=101.0,
            direction="max",
            prior=[100.0],
            noise_fraction=0.005,
        )
        is True
    )
    assert (
        scalar_candidate_retained(
            metric=100.2,
            direction="max",
            prior=[100.0],
            noise_fraction=0.005,
        )
        is False
    )
    assert (
        scalar_candidate_retained(
            metric=90.0,
            direction="min",
            prior=[100.0],
        )
        is True
    )


def test_orchestrator_strategy_update_is_separate_from_resolution() -> None:
    ledger = reconcile_hypothesis_ledger(
        None,
        [_official_round(1, 100.0, hypothesis_id="H-1")],
        None,
    )

    updated = apply_strategy_updates(
        ledger,
        [
            HypothesisStrategyUpdate(
                hypothesis_id="H-1",
                disposition="abandoned",
                reason="Official measurement regressed against its parent.",
            )
        ],
    )
    hypothesis = updated.by_id("H-1")

    assert hypothesis is not None
    assert hypothesis.resolution is HypothesisResolution.PROVEN
    assert hypothesis.strategy is HypothesisStrategyState.ABANDONED


def test_reconciliation_completes_active_projection_when_round_evidence_exists() -> None:
    round_record = _official_round(1, 100.0, hypothesis_id="H-1")
    stale = HypothesisLedger(hypotheses=[HypothesisRecord(hypothesis_id="H-1", started_round=1)])

    hypothesis = reconcile_hypothesis_ledger(stale, [round_record], None).by_id("H-1")

    assert hypothesis is not None
    assert hypothesis.strategy is HypothesisStrategyState.COMPLETED


def test_reconciliation_recovers_portable_active_without_local_checkpoint() -> None:
    saved_active = HypothesisLedger(
        hypotheses=[HypothesisRecord(hypothesis_id="H-1", started_round=1)]
    )

    hypothesis = reconcile_hypothesis_ledger(saved_active, [], None).by_id("H-1")

    assert hypothesis is not None
    assert hypothesis.strategy is HypothesisStrategyState.ACTIVE


def test_reconciliation_preserves_portable_active_after_observed_continuation() -> None:
    continued = RoundRecord(
        round_number=1,
        commit="a" * 40,
        perf_metric=None,
        perf_unit=None,
        passed=True,
        reviewed=True,
        hypothesis_id="H-1",
        hypothesis_declared_outcome="continue",
        hypothesis_outcome="continue",
    )
    saved_active = HypothesisLedger(
        hypotheses=[HypothesisRecord(hypothesis_id="H-1", started_round=1, rounds=[1])]
    )

    hypothesis = reconcile_hypothesis_ledger(saved_active, [continued], None).by_id("H-1")

    assert hypothesis is not None
    assert hypothesis.strategy is HypothesisStrategyState.ACTIVE


def test_reconciliation_replays_active_plan_strategy_updates() -> None:
    completed = RoundRecord(
        round_number=1,
        commit="a" * 40,
        perf_metric=None,
        perf_unit=None,
        passed=True,
        reviewed=True,
        hypothesis_id="old-direction",
        hypothesis_outcome="proven",
    )
    saved_before_start = HypothesisLedger(
        hypotheses=[
            HypothesisRecord(
                hypothesis_id="old-direction",
                started_round=1,
                strategy=HypothesisStrategyState.COMPLETED,
            )
        ]
    )
    active = ActiveHypothesis(
        plan=OrchestratorPlan(
            hypothesis_id="new-direction",
            hypothesis_updates=[
                HypothesisStrategyUpdate(
                    hypothesis_id="old-direction",
                    disposition="abandoned",
                    reason="The active plan superseded it.",
                )
            ],
            task="try the new direction",
            pass_criteria="review",  # noqa: S106  # tracked: #288
            reasoning="start a recoverable transition",
        ),
        started_round=2,
        parent_round=1,
        parent_commit="a" * 40,
    )

    ledger = reconcile_hypothesis_ledger(saved_before_start, [completed], active)

    old = ledger.by_id("old-direction")
    new = ledger.by_id("new-direction")
    assert old is not None
    assert old.strategy is HypothesisStrategyState.ABANDONED
    assert new is not None
    assert new.strategy is HypothesisStrategyState.ACTIVE


def test_legacy_round_with_unknown_direction_keeps_semantics_unknown() -> None:
    legacy = RoundRecord(
        round_number=1,
        commit="a" * 40,
        perf_metric=90.0,
        perf_unit="total_ops_per_sec",
        passed=True,
        reviewed=True,
        hypothesis_id="H-1",
        hypothesis_outcome="proven",
        official_evaluation=True,
    )

    hypothesis = reconcile_hypothesis_ledger(None, [legacy], None).by_id("H-1")

    assert hypothesis is not None
    assert hypothesis.resolution is HypothesisResolution.INCONCLUSIVE
    assert hypothesis.measurement is None
    assert hypothesis.candidate_retained is None


def test_legacy_prerequisite_disposition_is_retained() -> None:
    legacy = RoundRecord(
        round_number=1,
        commit="a" * 40,
        perf_metric=None,
        perf_unit=None,
        passed=True,
        hypothesis_id="H-1",
        candidate_disposition="prerequisite",
    )

    hypothesis = reconcile_hypothesis_ledger(None, [legacy], None).by_id("H-1")

    assert hypothesis is not None
    assert hypothesis.candidate_retained is True


def test_typed_unknown_retention_does_not_reuse_raw_disposition() -> None:
    typed = RoundRecord(
        round_number=1,
        commit="a" * 40,
        perf_metric=None,
        perf_unit=None,
        passed=True,
        reviewed=True,
        hypothesis_id="H-1",
        hypothesis_declared_outcome="supported",
        judge_verdict="pass",
        hypothesis_outcome="proven",
        candidate_disposition="pareto_frontier",
        candidate_retained=None,
    )

    hypothesis = reconcile_hypothesis_ledger(None, [typed], None).by_id("H-1")

    assert hypothesis is not None
    assert hypothesis.candidate_retained is None


def test_strategy_updates_reject_unknown_and_active_hypotheses() -> None:
    ledger = HypothesisLedger(
        hypotheses=[
            HypothesisRecord(
                hypothesis_id="completed",
                started_round=1,
                strategy=HypothesisStrategyState.COMPLETED,
            ),
            HypothesisRecord(hypothesis_id="active", started_round=2),
        ]
    )
    unknown = HypothesisStrategyUpdate(
        hypothesis_id="missing",
        disposition="parked",
        reason="No evidence.",
    )
    active = HypothesisStrategyUpdate(
        hypothesis_id="active",
        disposition="abandoned",
        reason="No evidence.",
    )

    with pytest.raises(ValueError, match="unknown hypothesis"):
        apply_strategy_updates(ledger, [unknown])
    with pytest.raises(ValueError, match="cannot abandoned active"):
        apply_strategy_updates(ledger, [active])


def test_ledger_rejects_duplicate_ids_and_multiple_active_hypotheses() -> None:
    with pytest.raises(ValueError, match="hypothesis IDs must be unique"):
        HypothesisLedger(
            hypotheses=[
                HypothesisRecord(hypothesis_id="H-1", started_round=1),
                HypothesisRecord(hypothesis_id="H-1", started_round=2),
            ]
        )
    with pytest.raises(ValueError, match="at most one hypothesis may be active"):
        HypothesisLedger(
            hypotheses=[
                HypothesisRecord(hypothesis_id="H-1", started_round=1),
                HypothesisRecord(hypothesis_id="H-2", started_round=2),
            ]
        )
