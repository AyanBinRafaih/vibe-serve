"""Assemble the hypothesis-level experiment log from persisted run state.

The agent loop already records everything this module needs: each completed
``RoundRecord`` carries its ``hypothesis_id`` and the resolved
``hypothesis_outcome``, and ``ActiveHypothesis`` carries the live plan for the
hypothesis currently under investigation. Nothing here adds tracking to the
loop; it only regroups what the loop wrote, from rounds to units of
investigation.

Both inputs arrive as the loop's own typed models, loaded by the caller through
the project store. This module never reads a file and never names one, so a
change to how a project lays its state out on disk cannot reach it.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Literal

from vibesys.server.protocol import HypothesisEntry, HypothesisRound

if TYPE_CHECKING:
    from collections.abc import Sequence

    from vibesys.loops.agent.model import ActiveHypothesis
    from vs_loop_state import RoundRecord

# Rendered in place of a missing ``hypothesis_id``. Log directories written
# before hypothesis tracking carry none, and those rounds must still appear.
UNIDENTIFIED = "(unidentified)"


def build_experiment_log(
    rounds: Sequence[RoundRecord],
    active: ActiveHypothesis | None = None,
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

    active_id = _active_id(active)
    entries = [
        _entry(identifier, records, identified=identified, active_id=active_id)
        for identifier, identified, records in groups
    ]

    # A hypothesis whose first round has not finished yet has no completed
    # round record. Surface it from the live plan so the operator sees the work
    # in flight rather than a table that stops at the last completed round.
    if (
        active is not None
        and active_id is not None
        and not any(entry.hypothesis_id == active_id and entry.identified for entry in entries)
    ):
        entries.append(_pending_entry(active_id, active))
    entries.sort(key=lambda entry: (entry.first_round, entry.hypothesis_id))
    return entries


def _entry(
    identifier: str,
    records: Sequence[RoundRecord],
    *,
    identified: bool,
    active_id: str | None,
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
        claim=_text(closing.hypothesis_claim),
        action=_text(closing.hypothesis_task),
        first_round=min(numbers),
        last_round=max(numbers),
        rounds=[_round(record) for record in records],
        # Copied from the loop's own resolution, never recomputed. An active
        # hypothesis mid-continuation legitimately has no terminal value yet.
        resolved_outcome=None if active else _text(closing.hypothesis_outcome),
        judge_verdict=None if active else _verdict(closing),
        perf_metric=metric,
        perf_unit=_text(unit),
        perf_delta_pct=None,
        kept=any(_kept(record) for record in records),
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


def apply_baselines(entries: Sequence[HypothesisEntry], rounds: Sequence[RoundRecord]) -> None:
    """Fill ``perf_delta_pct`` against the last measurement before each entry.

    Kept separate from grouping because the baseline for one hypothesis is a
    property of the whole run, not of the rounds inside that hypothesis.
    """
    measured = sorted(
        (
            (record.round_number, value)
            for record in rounds
            if (value := _finite(record.perf_metric)) is not None
        ),
        key=lambda item: item[0],
    )
    for entry in entries:
        if entry.perf_metric is None:
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
    if record.passed:
        return "pass"
    # An unreviewed round is provisional: sparse-review policy deferred the
    # judge, so there is no verdict to report rather than a failing one.
    return "fail" if record.reviewed else None


def _kept(record: RoundRecord) -> bool:
    return record.official_evaluation or record.candidate_disposition == "pareto_frontier"


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
