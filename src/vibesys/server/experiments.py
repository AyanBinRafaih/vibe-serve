"""Project authoritative agent-run state into experiment-log protocol entries.

The agent loop owns hypothesis lifecycle state. This module is deliberately a
one-way projection: it does not group rounds, select a baseline, or infer a
resolution from individual round fields.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from vibesys.schemas import derive_hypothesis_title
from vibesys.server.protocol import HypothesisEntry, HypothesisRound

if TYPE_CHECKING:
    from vibesys.loops.agent.model import AgentRunState, Hypothesis
    from vs_loop_state import RoundRecord


def build_experiment_log(state: AgentRunState) -> list[HypothesisEntry]:
    """Return the complete hypothesis history in stable start-round order."""
    return sorted(
        (
            _entry(hypothesis, active_id=state.active_hypothesis_id)
            for hypothesis in state.hypotheses
        ),
        key=lambda entry: (entry.first_round, entry.hypothesis_id),
    )


def _entry(hypothesis: Hypothesis, *, active_id: str | None) -> HypothesisEntry:
    """Copy one domain hypothesis into its presentation-neutral DTO."""
    rounds = hypothesis.rounds
    measurement = hypothesis.measurement
    return HypothesisEntry(
        hypothesis_id=hypothesis.hypothesis_id,
        title=_text(hypothesis.plan.title) or derive_hypothesis_title(hypothesis.plan.hypothesis),
        claim=_text(hypothesis.plan.hypothesis),
        action=_text(hypothesis.plan.task),
        first_round=hypothesis.started_round,
        last_round=rounds[-1].round_number if rounds else hypothesis.started_round,
        rounds=[_round(record) for record in rounds],
        resolved_outcome=(
            hypothesis.resolution.value if hypothesis.resolution is not None else None
        ),
        judge_verdict=_judge_verdict(hypothesis),
        # The measurement fields are intentionally copied as one tuple.
        # Choosing a newer per-round metric here would pair it with a
        # different causal delta and make the UI lie about the measurement.
        perf_metric=measurement.value if measurement is not None else None,
        perf_unit=_text(measurement.unit) if measurement is not None else None,
        perf_delta_pct=measurement.delta_pct if measurement is not None else None,
        perf_metric_name=_text(measurement.metric) if measurement is not None else None,
        perf_direction=measurement.direction if measurement is not None else None,
        perf_baseline_value=measurement.baseline_value if measurement is not None else None,
        kept=hypothesis.candidate_retained,
        strategy_disposition=hypothesis.strategy.value,
        strategy_reason=hypothesis.strategy_reason,
        active=hypothesis.hypothesis_id == active_id,
    )


def _round(record: RoundRecord) -> HypothesisRound:
    return HypothesisRound(
        round=record.round_number,
        passed=record.passed,
        reviewed=record.reviewed,
        hypothesis_outcome=_text(record.hypothesis_outcome),
        perf_metric=record.perf_metric,
        perf_unit=_text(record.perf_unit),
        commit=_text(record.commit),
        official_evaluation=record.official_evaluation,
        candidate_disposition=record.candidate_disposition,
    )


def _judge_verdict(hypothesis: Hypothesis) -> Literal["pass", "fail"] | None:
    value = hypothesis.review.value
    return value if value in ("pass", "fail") else None


def _text(value: str | None) -> str | None:
    return value or None
