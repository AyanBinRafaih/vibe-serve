"""Assemble the hypothesis-level experiment log from persisted run state.

The agent loop already records everything this module needs: ``rounds.json``
holds one record per completed round including its ``hypothesis_id`` and the
resolved ``hypothesis_outcome``, and ``active_hypothesis.json`` holds the live
plan for the hypothesis currently under investigation. Nothing here adds
tracking to the loop; it only regroups what the loop wrote, from rounds to
units of investigation.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Literal

from vibesys.server.protocol import HypothesisEntry, HypothesisRound

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

# Rendered in place of a missing ``hypothesis_id``. Log directories written
# before hypothesis tracking carry none, and those rounds must still appear.
UNIDENTIFIED = "(unidentified)"


def build_experiment_log(
    rounds: Sequence[Mapping[str, object]],
    active: Mapping[str, object] | None = None,
) -> list[HypothesisEntry]:
    """Group round records into one entry per hypothesis.

    Rounds are grouped by ``hypothesis_id`` and ordered by the first round each
    hypothesis touched, so an entry keeps its position as later rounds land.
    A round with no id cannot be attributed to a hypothesis and becomes its own
    placeholder entry rather than being merged with unrelated rounds or dropped.
    """
    ordered = sorted(rounds, key=_round_number)
    groups: list[tuple[str, bool, list[Mapping[str, object]]]] = []
    by_id: dict[str, list[Mapping[str, object]]] = {}
    for record in ordered:
        raw_id = record.get("hypothesis_id")
        identifier = raw_id if isinstance(raw_id, str) and raw_id else None
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

    # A hypothesis whose first round has not finished yet has no record in
    # rounds.json. Surface it from the live plan so the operator sees the work
    # in flight rather than a table that stops at the last completed round.
    if active_id is not None and not any(
        entry.hypothesis_id == active_id and entry.identified for entry in entries
    ):
        entries.append(_pending_entry(active_id, active))
    entries.sort(key=lambda entry: (entry.first_round, entry.hypothesis_id))
    return entries


def _entry(
    identifier: str,
    records: Sequence[Mapping[str, object]],
    *,
    identified: bool,
    active_id: str | None,
) -> HypothesisEntry:
    numbers = [_round_number(record) for record in records]
    closing = records[-1]
    metric_record = _latest_measured(records)
    metric = _finite(metric_record.get("perf_metric")) if metric_record else None
    unit = metric_record.get("perf_unit") if metric_record else None
    active = identified and identifier == active_id
    return HypothesisEntry(
        hypothesis_id=identifier,
        identified=identified,
        claim=_text(closing.get("hypothesis_claim")),
        action=_text(closing.get("hypothesis_task")),
        first_round=min(numbers),
        last_round=max(numbers),
        rounds=[_round(record) for record in records],
        # Copied from the loop's own resolution, never recomputed. An active
        # hypothesis mid-continuation legitimately has no terminal value yet.
        resolved_outcome=None if active else _text(closing.get("hypothesis_outcome")),
        judge_verdict=None if active else _verdict(closing),
        perf_metric=metric,
        perf_unit=unit if isinstance(unit, str) and unit else None,
        perf_delta_pct=None,
        kept=any(_kept(record) for record in records),
        active=active,
    )


def _pending_entry(active_id: str, active: Mapping[str, object] | None) -> HypothesisEntry:
    raw_plan = (active or {}).get("plan")
    plan: Mapping[str, object] = raw_plan if isinstance(raw_plan, dict) else {}
    started = (active or {}).get("started_round")
    round_number = int(started) if isinstance(started, int) and not isinstance(started, bool) else 0
    return HypothesisEntry(
        hypothesis_id=active_id,
        claim=_text(plan.get("hypothesis")),
        action=_text(plan.get("task")),
        first_round=round_number,
        last_round=round_number,
        active=True,
    )


def _round(record: Mapping[str, object]) -> HypothesisRound:
    disposition = record.get("candidate_disposition")
    return HypothesisRound(
        round=_round_number(record),
        passed=bool(record.get("passed", False)),
        reviewed=bool(record.get("reviewed", True)),
        hypothesis_outcome=_text(record.get("hypothesis_outcome")),
        perf_metric=_finite(record.get("perf_metric")),
        perf_unit=_text(record.get("perf_unit")),
        commit=_text(record.get("commit")),
        official_evaluation=bool(record.get("official_evaluation", False)),
        candidate_disposition=disposition if isinstance(disposition, str) else None,
    )


def apply_baselines(
    entries: Sequence[HypothesisEntry], rounds: Sequence[Mapping[str, object]]
) -> None:
    """Fill ``perf_delta_pct`` against the last measurement before each entry.

    Kept separate from grouping because the baseline for one hypothesis is a
    property of the whole run, not of the rounds inside that hypothesis.
    """
    measured = sorted(
        (
            (_round_number(record), value)
            for record in rounds
            if (value := _finite(record.get("perf_metric"))) is not None
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


def _latest_measured(records: Sequence[Mapping[str, object]]) -> Mapping[str, object] | None:
    for record in reversed(records):
        if _finite(record.get("perf_metric")) is not None:
            return record
    return None


def _verdict(record: Mapping[str, object]) -> Literal["pass", "fail"] | None:
    if record.get("passed", False):
        return "pass"
    # An unreviewed round is provisional: sparse-review policy deferred the
    # judge, so there is no verdict to report rather than a failing one.
    return "fail" if bool(record.get("reviewed", True)) else None


def _kept(record: Mapping[str, object]) -> bool:
    return bool(record.get("official_evaluation", False)) or (
        record.get("candidate_disposition") == "pareto_frontier"
    )


def _active_id(active: Mapping[str, object] | None) -> str | None:
    plan = (active or {}).get("plan")
    if not isinstance(plan, dict):
        return None
    identifier = plan.get("hypothesis_id")
    return identifier if isinstance(identifier, str) and identifier else None


def _round_number(record: Mapping[str, object]) -> int:
    value = record.get("round", 0)
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        return 0
    try:
        return int(value)
    except ValueError:
        return 0


def _finite(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None
