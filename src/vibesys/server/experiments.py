"""Assemble the hypothesis-level experiment log from persisted run state.

The typed hypothesis ledger supplies lifecycle decisions, completed
``RoundRecord`` values supply immutable evidence, and ``ActiveHypothesis`` is
the operational continuation checkpoint. Nothing here adds tracking or
reclassifies evidence; it only projects those backend models for clients.

Inputs arrive as the loop's own typed models, loaded by the caller through
the project store. This module never reads a file and never names one, so a
change to how a project lays its state out on disk cannot reach it.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Literal

from vibesys.server.protocol import HypothesisEntry, HypothesisRound

if TYPE_CHECKING:
    from collections.abc import Sequence

    from vibesys.loops.agent.model import ActiveHypothesis, HypothesisLedger, HypothesisRecord
    from vs_loop_state import RoundRecord

# Rendered in place of a missing ``hypothesis_id``. Log directories written
# before hypothesis tracking carry none, and those rounds must still appear.
UNIDENTIFIED = "(unidentified)"


def build_experiment_log(
    rounds: Sequence[RoundRecord],
    active: ActiveHypothesis | None = None,
    ledger: HypothesisLedger | None = None,
) -> list[HypothesisEntry]:
    """Group round records into one entry per hypothesis.

    Rounds are grouped by ``hypothesis_id`` and ordered by the first round each
    hypothesis touched, so an entry keeps its position as later rounds land.
    A round with no id cannot be attributed to a hypothesis and becomes its own
    placeholder entry rather than being merged with unrelated rounds or dropped.
    """
    ordered = sorted(rounds, key=lambda record: record.round_number)
    groups: list[tuple[str, bool, list[RoundRecord]]] = []
    by_id: dict[str, list[RoundRecord]] = {}
    for record in ordered:
        identifier = record.hypothesis_id or None
        if identifier is None:
            groups.append((UNIDENTIFIED, False, [record]))
            continue
        existing = by_id.get(identifier)
        if existing is None:
            existing = []
            by_id[identifier] = existing
            groups.append((identifier, True, existing))
        existing.append(record)

    ledger_by_id = (
        {item.hypothesis_id: item for item in ledger.hypotheses} if ledger is not None else {}
    )
    ledger_active = next(
        (item for item in ledger_by_id.values() if item.strategy.value == "active"),
        None,
    )
    active_id = ledger_active.hypothesis_id if ledger_active is not None else _active_id(active)
    entries = [
        _entry(
            identifier,
            records,
            identified=identified,
            active_id=active_id,
            state=ledger_by_id.get(identifier),
        )
        for identifier, identified, records in groups
    ]

    # A hypothesis whose first round has not finished yet has no completed
    # round record. Surface it from the live plan so the operator sees the work
    # in flight rather than a table that stops at the last completed round.
    if active_id is not None and not any(
        entry.hypothesis_id == active_id and entry.identified for entry in entries
    ):
        if ledger_active is not None:
            entries.append(_pending_ledger_entry(ledger_active))
        elif active is not None:
            entries.append(_pending_entry(active_id, active))
    entries.sort(key=lambda entry: (entry.first_round, entry.hypothesis_id))
    return entries


def _entry(
    identifier: str,
    records: Sequence[RoundRecord],
    *,
    identified: bool,
    active_id: str | None,
    state: HypothesisRecord | None,
) -> HypothesisEntry:
    numbers = [record.round_number for record in records]
    closing = records[-1]
    metric_record = _latest_measured(records)
    metric = _finite(metric_record.perf_metric) if metric_record else None
    unit = metric_record.perf_unit if metric_record else None
    active = identified and identifier == active_id
    return HypothesisEntry(
        hypothesis_id=identifier,
        identified=identified,
        claim=_text(state.claim if state is not None else closing.hypothesis_claim),
        action=_text(state.task if state is not None else closing.hypothesis_task),
        first_round=min(numbers),
        last_round=max(numbers),
        rounds=[_round(record) for record in records],
        # Copied from the loop's own resolution, never recomputed. An active
        # hypothesis mid-continuation legitimately has no terminal value yet.
        resolved_outcome=None if active else _resolved_outcome(state, closing),
        judge_verdict=(
            None if active else _ledger_verdict(state) if state is not None else _verdict(closing)
        ),
        perf_metric=metric,
        perf_unit=_text(unit),
        perf_delta_pct=(
            state.measurement.delta_pct
            if state is not None and state.measurement is not None
            else None
        ),
        kept=_candidate_retention(state, records),
        strategy_disposition=(state.strategy.value if state is not None else None),
        strategy_reason=(state.strategy_reason if state is not None else None),
        active=active,
    )


def _pending_entry(active_id: str, active: ActiveHypothesis) -> HypothesisEntry:
    return HypothesisEntry(
        hypothesis_id=active_id,
        claim=_text(active.plan.hypothesis),
        action=_text(active.plan.task),
        first_round=active.started_round,
        last_round=active.started_round,
        active=True,
    )


def _pending_ledger_entry(state: HypothesisRecord) -> HypothesisEntry:
    return HypothesisEntry(
        hypothesis_id=state.hypothesis_id,
        claim=_text(state.claim),
        action=_text(state.task),
        first_round=state.started_round,
        last_round=state.started_round,
        strategy_disposition=state.strategy.value,
        strategy_reason=state.strategy_reason,
        active=True,
    )


def _round(record: RoundRecord) -> HypothesisRound:
    return HypothesisRound(
        round=record.round_number,
        passed=record.passed,
        reviewed=record.reviewed,
        hypothesis_outcome=_text(record.hypothesis_outcome),
        perf_metric=_finite(record.perf_metric),
        perf_unit=_text(record.perf_unit),
        commit=_text(record.commit),
        official_evaluation=record.official_evaluation,
        candidate_disposition=record.candidate_disposition,
    )


def apply_baselines(
    entries: Sequence[HypothesisEntry],
    rounds: Sequence[RoundRecord],
    ledger: HypothesisLedger | None = None,
) -> None:
    """Fill ``perf_delta_pct`` against the last measurement before each entry.

    Kept separate from grouping because the baseline for one hypothesis is a
    property of the whole run, not of the rounds inside that hypothesis. This
    legacy projection is intentionally not applied to ledger-owned records:
    their measurement's causal baseline is established by the loop, and a
    missing value must remain unknown rather than become a chronological guess.
    """
    ledger_ids = (
        {record.hypothesis_id for record in ledger.hypotheses} if ledger is not None else set()
    )
    measured = sorted(
        (
            (record.round_number, value)
            for record in rounds
            if (value := _finite(record.perf_metric)) is not None
        ),
        key=lambda item: item[0],
    )
    for entry in entries:
        if (
            entry.hypothesis_id in ledger_ids
            or entry.perf_metric is None
            or entry.perf_delta_pct is not None
        ):
            continue
        baseline = next(
            (value for number, value in reversed(measured) if number < entry.first_round),
            None,
        )
        if baseline is None or baseline == 0:
            continue
        entry.perf_delta_pct = (entry.perf_metric - baseline) / abs(baseline) * 100


def _latest_measured(records: Sequence[RoundRecord]) -> RoundRecord | None:
    for record in reversed(records):
        if _finite(record.perf_metric) is not None:
            return record
    return None


def _verdict(record: RoundRecord) -> Literal["pass", "fail"] | None:
    if record.judge_verdict == "pass":
        return "pass"
    if record.judge_verdict == "fail":
        return "fail"
    if record.passed:
        return "pass"
    # An unreviewed round is provisional: sparse-review policy deferred the
    # judge, so there is no verdict to report rather than a failing one.
    return "fail" if record.reviewed else None


def _ledger_verdict(state: HypothesisRecord) -> Literal["pass", "fail"] | None:
    value = state.review.value
    return value if value in ("pass", "fail") else None


def _resolved_outcome(state: HypothesisRecord | None, closing: RoundRecord) -> str | None:
    """Return typed ledger resolution, falling back only for pre-ledger runs."""
    if state is not None:
        # A ledger record is authoritative even while its resolution is
        # unknown. A stale round-level declaration cannot complete it.
        return state.resolution.value if state.resolution is not None else None
    return _text(closing.hypothesis_outcome)


def _candidate_retention(
    state: HypothesisRecord | None,
    records: Sequence[RoundRecord],
) -> bool | None:
    if state is not None:
        return state.candidate_retained
    explicit = next(
        (
            record.candidate_retained
            for record in reversed(records)
            if record.candidate_retained is not None
        ),
        None,
    )
    if explicit is not None:
        return explicit
    disposition = records[-1].candidate_disposition
    if disposition in {"pareto_frontier", "prerequisite"}:
        return True
    if disposition == "discard":
        return False
    return None


def _active_id(active: ActiveHypothesis | None) -> str | None:
    if active is None:
        return None
    return active.plan.hypothesis_id or None


def _finite(value: float | None) -> float | None:
    # The loop rejects non-finite metrics before persistence, but a stored run
    # predating that guard must degrade to "no measurement" rather than crash
    # the operator's view on a protocol validation error.
    if value is None:
        return None
    return value if math.isfinite(value) else None


def _text(value: str | None) -> str | None:
    return value or None
