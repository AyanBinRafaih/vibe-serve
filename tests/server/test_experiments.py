"""Grouping, outcome mapping, and integration flags for the experiment log."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from vibesys.server import EventType, RunSupervisor
from vibesys.server.experiments import UNIDENTIFIED, apply_baselines, build_experiment_log
from vibesys.server.protocol import ExperimentQuery
from vibesys.server.service import SupervisionService

if TYPE_CHECKING:
    from pathlib import Path


def _round(number: int, **overrides: object) -> dict[str, object]:
    record: dict[str, object] = {
        "round": number,
        "commit": f"c{number}",
        "perf_metric": None,
        "perf_unit": None,
        "passed": False,
        "reviewed": True,
        "hypothesis_id": None,
        "hypothesis_outcome": None,
        "official_evaluation": False,
        "candidate_disposition": "unassessed",
    }
    record.update(overrides)
    return record


def test_continuation_rounds_collapse_into_one_entry() -> None:
    rounds = [
        _round(
            7,
            hypothesis_id="H-08",
            hypothesis_claim="bigger KV cache block",
            hypothesis_task="grow the block size",
            hypothesis_outcome="continue",
            reviewed=False,
        ),
        _round(
            8,
            hypothesis_id="H-08",
            hypothesis_claim="bigger KV cache block",
            hypothesis_task="grow the block size",
            hypothesis_outcome="rejected",
        ),
    ]

    (entry,) = build_experiment_log(rounds)

    assert entry.hypothesis_id == "H-08"
    assert (entry.first_round, entry.last_round) == (7, 8)
    assert [item.round for item in entry.rounds] == [7, 8]
    assert entry.claim == "bigger KV cache block"
    assert entry.action == "grow the block size"


def test_resolved_outcome_is_copied_from_the_closing_round() -> None:
    """The loop already resolved proven/rejected; the log must not re-derive it."""
    rounds = [
        _round(1, hypothesis_id="H-01", hypothesis_outcome="proven", passed=True),
        _round(2, hypothesis_id="H-02", hypothesis_outcome="rejected"),
        _round(3, hypothesis_id="H-03", hypothesis_outcome="disproven", reviewed=False),
    ]

    entries = build_experiment_log(rounds)

    assert [entry.resolved_outcome for entry in entries] == ["proven", "rejected", "disproven"]
    # A deferred judge is not a failing judge.
    assert [entry.judge_verdict for entry in entries] == ["pass", "fail", None]


def test_active_hypothesis_is_marked_and_left_unresolved() -> None:
    rounds = [
        _round(4, hypothesis_id="H-09", hypothesis_outcome="continue", reviewed=False),
    ]
    active = {
        "plan": {"hypothesis_id": "H-09", "hypothesis": "retry with tuning"},
        "started_round": 4,
    }

    (entry,) = build_experiment_log(rounds, active)

    assert entry.active is True
    assert entry.resolved_outcome is None
    assert entry.judge_verdict is None


def test_active_hypothesis_appears_before_its_first_round_finishes() -> None:
    active = {
        "plan": {"hypothesis_id": "H-10", "hypothesis": "prefetch weights", "task": "add prefetch"},
        "started_round": 12,
    }

    entries = build_experiment_log(
        [_round(11, hypothesis_id="H-09", hypothesis_outcome="proven")], active
    )

    assert [entry.hypothesis_id for entry in entries] == ["H-09", "H-10"]
    assert entries[1].active is True
    assert entries[1].claim == "prefetch weights"
    assert entries[1].rounds == []


def test_records_without_a_hypothesis_id_render_as_separate_placeholders() -> None:
    """Old log directories predate hypothesis tracking and must not be dropped."""
    rounds = [_round(1), _round(2)]

    entries = build_experiment_log(rounds)

    assert [entry.hypothesis_id for entry in entries] == [UNIDENTIFIED, UNIDENTIFIED]
    assert [entry.identified for entry in entries] == [False, False]
    assert [entry.first_round for entry in entries] == [1, 2]


def test_entries_are_ordered_by_first_round_and_do_not_reshuffle() -> None:
    rounds = [
        _round(3, hypothesis_id="H-B"),
        _round(1, hypothesis_id="H-A"),
        _round(4, hypothesis_id="H-A"),
        _round(2, hypothesis_id="H-B"),
    ]

    entries = build_experiment_log(rounds)

    assert [entry.hypothesis_id for entry in entries] == ["H-A", "H-B"]
    assert [(entry.first_round, entry.last_round) for entry in entries] == [(1, 4), (2, 3)]


def test_kept_is_independent_of_the_judge_verdict() -> None:
    """A judged-good hypothesis need not be integrated, and vice versa."""
    rounds = [
        _round(1, hypothesis_id="H-A", hypothesis_outcome="proven", passed=True),
        _round(
            2,
            hypothesis_id="H-B",
            hypothesis_outcome="rejected",
            candidate_disposition="pareto_frontier",
        ),
        _round(
            3,
            hypothesis_id="H-C",
            hypothesis_outcome="proven",
            passed=True,
            official_evaluation=True,
        ),
    ]

    entries = build_experiment_log(rounds)

    by_id = {entry.hypothesis_id: entry for entry in entries}
    # Proven but never gated or retained: accepted as a claim, not integrated.
    assert by_id["H-A"].kept is False
    # Rejected claim whose candidate still earned a place on the frontier.
    assert by_id["H-B"].kept is True
    assert by_id["H-C"].kept is True


def test_measured_delta_uses_the_last_measurement_before_the_hypothesis() -> None:
    rounds = [
        _round(1, hypothesis_id="H-A", perf_metric=100.0, perf_unit="tok_s"),
        _round(2, hypothesis_id="H-B", perf_metric=112.0, perf_unit="tok_s"),
    ]

    entries = build_experiment_log(rounds)
    apply_baselines(entries, rounds)

    assert entries[0].perf_delta_pct is None
    assert entries[1].perf_metric == 112.0
    assert entries[1].perf_delta_pct == 12.0


def test_service_reads_both_state_files(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "rounds.json").write_text(
        json.dumps([_round(1, hypothesis_id="H-01", hypothesis_outcome="proven", passed=True)])
    )
    (logs / "active_hypothesis.json").write_text(
        json.dumps(
            {"plan": {"hypothesis_id": "H-02", "hypothesis": "next idea"}, "started_round": 2}
        )
    )
    supervisor = RunSupervisor()
    supervisor.attach(logs)

    response = SupervisionService(supervisor).execute(ExperimentQuery())

    assert [entry.hypothesis_id for entry in response.experiments] == ["H-01", "H-02"]
    assert response.experiments[1].active is True
    recorded = [event.type for event in supervisor.read_events()]
    assert EventType.STATUS_QUERY.value in recorded


def test_service_tolerates_a_missing_or_unreadable_log_dir(tmp_path: Path) -> None:
    supervisor = RunSupervisor()
    supervisor.attach(tmp_path)
    service = SupervisionService(supervisor)

    assert service.experiments() == []

    (tmp_path / "rounds.json").write_text("{ truncated")
    assert service.experiments() == []
