"""Grouping, outcome mapping, and integration flags for the experiment log."""

from __future__ import annotations

from typing import TYPE_CHECKING

from vibesys.loops.agent.model import (
    ActiveHypothesis,
    HypothesisLedger,
    HypothesisMeasurement,
    HypothesisRecord,
    HypothesisResolution,
    HypothesisReview,
    HypothesisStrategyState,
)
from vibesys.loops.agent.state import AgentStateStore, HypothesisLedgerStore
from vibesys.run.state import RunStateNamespace
from vibesys.schemas import OrchestratorPlan
from vibesys.server import EventType, RunSupervisor
from vibesys.server.experiments import UNIDENTIFIED, apply_baselines, build_experiment_log
from vibesys.server.protocol import ExperimentQuery
from vibesys.server.service import SupervisionService
from vs_loop_state import RoundRecord
from vs_project import AgentRunConfiguration, PlainRunConfiguration, Project, RunEnvironmentRecord

if TYPE_CHECKING:
    from pathlib import Path


def _round(number: int, **overrides: object) -> RoundRecord:
    fields: dict[str, object] = {
        "round_number": number,
        "commit": f"c{number}",
        "perf_metric": None,
        "perf_unit": None,
        "passed": False,
    }
    fields.update(overrides)
    return RoundRecord(**fields)  # type: ignore[arg-type]


def _active(
    hypothesis_id: str,
    started_round: int,
    *,
    hypothesis: str = "",
    task: str = "",
) -> ActiveHypothesis:
    return ActiveHypothesis(
        plan=OrchestratorPlan(
            hypothesis_id=hypothesis_id,
            hypothesis=hypothesis,
            task=task,
            pass_criteria="",
            reasoning="",
        ),
        started_round=started_round,
    )


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
    active = _active("H-09", 4, hypothesis="retry with tuning")

    (entry,) = build_experiment_log(rounds, active)

    assert entry.active is True
    assert entry.resolved_outcome is None
    assert entry.judge_verdict is None


def test_active_hypothesis_appears_before_its_first_round_finishes() -> None:
    active = _active("H-10", 12, hypothesis="prefetch weights", task="add prefetch")

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
        _round(
            1,
            hypothesis_id="H-A",
            hypothesis_outcome="proven",
            passed=True,
            candidate_disposition="pareto_frontier",
            candidate_retained=False,
        ),
        _round(
            2,
            hypothesis_id="H-B",
            hypothesis_outcome="rejected",
            candidate_disposition="pareto_frontier",
            candidate_retained=True,
        ),
        _round(
            3,
            hypothesis_id="H-C",
            hypothesis_outcome="proven",
            passed=True,
            official_evaluation=True,
            candidate_retained=True,
        ),
    ]

    entries = build_experiment_log(rounds)

    by_id = {entry.hypothesis_id: entry for entry in entries}
    # Proven but never gated or retained: accepted as a claim, not integrated.
    assert by_id["H-A"].kept is False
    # Rejected claim whose candidate still earned a place on the frontier.
    assert by_id["H-B"].kept is True
    assert by_id["H-C"].kept is True


def test_ledger_is_authoritative_for_the_experiment_projection() -> None:
    """A display projection must not recreate lifecycle decisions from rounds."""
    rounds = [
        _round(
            4,
            hypothesis_id="H-04",
            hypothesis_claim="stale claim",
            hypothesis_task="stale task",
            hypothesis_outcome="proven",
            passed=False,
            reviewed=False,
            perf_metric=90.0,
            perf_unit="ops_s",
        )
    ]
    ledger = HypothesisLedger(
        hypotheses=[
            HypothesisRecord(
                hypothesis_id="H-04",
                claim="use bounded batches",
                task="cap the batch size",
                started_round=4,
                rounds=[4],
                review=HypothesisReview.PASS,
                resolution=HypothesisResolution.DISPROVEN,
                measurement=HypothesisMeasurement(
                    round=4,
                    metric="throughput",
                    value=90.0,
                    unit="ops_s",
                    direction="max",
                    baseline_round=2,
                    baseline_commit="c2",
                    baseline_value=100.0,
                    delta_pct=-10.0,
                ),
                candidate_retained=False,
                strategy=HypothesisStrategyState.ABANDONED,
                strategy_reason="Official throughput regressed.",
            )
        ]
    )

    (entry,) = build_experiment_log(rounds, ledger=ledger)

    assert entry.claim == "use bounded batches"
    assert entry.action == "cap the batch size"
    assert entry.resolved_outcome == "disproven"
    assert entry.judge_verdict == "pass"
    assert entry.kept is False
    assert entry.perf_delta_pct == -10.0
    assert entry.strategy_disposition == "abandoned"
    assert entry.strategy_reason == "Official throughput regressed."


def test_ledger_does_not_fall_back_to_a_round_level_outcome() -> None:
    """Pending ledger state remains pending despite stale round declarations."""
    rounds = [
        _round(
            4,
            hypothesis_id="H-04",
            hypothesis_outcome="proven",
            passed=True,
            reviewed=True,
        )
    ]
    ledger = HypothesisLedger(
        hypotheses=[
            HypothesisRecord(
                hypothesis_id="H-04",
                started_round=4,
                rounds=[4],
                review=HypothesisReview.DEFERRED,
                resolution=None,
                strategy=HypothesisStrategyState.COMPLETED,
            )
        ]
    )

    (entry,) = build_experiment_log(rounds, ledger=ledger)

    assert entry.resolved_outcome is None
    assert entry.judge_verdict is None


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


def test_ledger_measurement_does_not_use_a_chronological_fallback_baseline() -> None:
    rounds = [
        _round(1, hypothesis_id="H-A", perf_metric=100.0, perf_unit="tok_s"),
        _round(2, hypothesis_id="H-B", perf_metric=112.0, perf_unit="tok_s"),
    ]
    ledger = HypothesisLedger(
        hypotheses=[
            HypothesisRecord(
                hypothesis_id="H-B",
                started_round=2,
                rounds=[2],
                measurement=HypothesisMeasurement(
                    round=2,
                    metric="throughput",
                    value=112.0,
                    unit="tok_s",
                    direction="max",
                ),
                strategy=HypothesisStrategyState.COMPLETED,
            )
        ]
    )

    entries = build_experiment_log(rounds, ledger=ledger)
    apply_baselines(entries, rounds, ledger)

    by_id = {entry.hypothesis_id: entry for entry in entries}
    assert by_id["H-B"].perf_delta_pct is None


def _agent_configuration() -> AgentRunConfiguration:
    return AgentRunConfiguration(
        outer_loop="agent",
        inner_loop="single-agent",
        interface="inprocess",
        agent_backend="stub",
        compute_backend="cpu",
        profiler="none",
        max_rounds=3,
        max_retries_per_round=1,
        judge_every=1,
        official_eval_every=1,
        memory_layout="files",
        run_environment=RunEnvironmentRecord(name="local"),
    )


def _plain_configuration() -> PlainRunConfiguration:
    return PlainRunConfiguration(
        outer_loop="plain",
        agent_backend="stub",
        compute_backend="cpu",
        profiler="none",
        max_rounds=3,
        max_attempts_per_issue=1,
        max_issues_per_perf_eval=1,
        run_environment=RunEnvironmentRecord(name="local"),
    )


def _project_run(
    project: Path,
    configuration: AgentRunConfiguration | PlainRunConfiguration | None = None,
) -> tuple[Project, str]:
    """Create a real project run, as ``tests/test_tui.py`` does."""
    project.mkdir()
    (project / "OBJECTIVE.md").write_text("Make the queue fast.\n", encoding="utf-8")
    vibesys_project = Project.open(project)
    state = vibesys_project.state
    state.create_project("queue")
    manifest = state.new_run_manifest(
        "queue",
        run_id="queue-run",
        branch="vibesys/queue-run",
        vibesys_version="0.2.0-test",
        configuration=configuration or _agent_configuration(),
        trusted_input_baseline="0" * 40,
    )
    state.create_run(manifest)
    return vibesys_project, manifest.run_id


def test_service_reads_rounds_and_the_active_plan_through_the_store(tmp_path: Path) -> None:
    """The service must reach state through the store, not through file paths.

    Round records are portable state and the active plan is machine-local, so
    this covers both halves of the store API the log depends on.
    """
    project, run_id = _project_run(tmp_path / "project")
    project.state.save_round(
        run_id, _round(1, hypothesis_id="H-01", hypothesis_outcome="proven", passed=True)
    )
    namespace = project.state.local_namespace(run_id, RunStateNamespace.AGENT)
    AgentStateStore(namespace).save_active(_active("H-02", 2, hypothesis="next idea"))
    supervisor = RunSupervisor()
    supervisor.attach(project.state.log_directory(run_id), project=project, run_id=run_id)

    response = SupervisionService(supervisor).execute(ExperimentQuery())

    assert [entry.hypothesis_id for entry in response.experiments] == ["H-01", "H-02"]
    assert response.experiments[1].active is True
    assert response.experiments_ready is True
    recorded = [event.type for event in supervisor.read_events()]
    assert EventType.STATUS_QUERY.value in recorded


def test_service_loads_portable_hypothesis_ledger(tmp_path: Path) -> None:
    """Portable strategy is retained when the service reconciles round evidence."""
    project, run_id = _project_run(tmp_path / "project")
    project.state.save_round(
        run_id,
        _round(
            1,
            hypothesis_id="H-01",
            hypothesis_outcome="disproven",
            reviewed=True,
            official_evaluation=True,
            perf_metric=97.0,
            perf_unit="ops_s",
            candidate_retained=False,
        ),
    )
    portable = project.state.portable_namespace(run_id, "agent")
    HypothesisLedgerStore(portable).save(
        HypothesisLedger(
            hypotheses=[
                HypothesisRecord(
                    hypothesis_id="H-01",
                    claim="portable hypothesis",
                    task="measure it",
                    started_round=1,
                    rounds=[1],
                    review=HypothesisReview.PASS,
                    resolution=HypothesisResolution.DISPROVEN,
                    candidate_retained=False,
                    strategy=HypothesisStrategyState.PARKED,
                    strategy_reason="Try a different prerequisite first.",
                )
            ]
        )
    )
    supervisor = RunSupervisor()
    supervisor.attach(project.state.log_directory(run_id), project=project, run_id=run_id)

    (entry,) = SupervisionService(supervisor).execute(ExperimentQuery()).experiments

    assert entry.resolved_outcome == "disproven"
    assert entry.kept is False
    assert entry.strategy_disposition == "parked"
    assert entry.strategy_reason == "Try a different prerequisite first."


def test_service_migrates_legacy_regression_with_persisted_objective_direction(
    tmp_path: Path,
) -> None:
    configuration = _agent_configuration().model_copy(
        update={"objectives": ("total_ops_per_sec:max",)}
    )
    project, run_id = _project_run(tmp_path / "project", configuration=configuration)
    project.state.save_round(
        run_id,
        _round(
            1,
            commit="a" * 40,
            hypothesis_id="queue-parent",
            hypothesis_outcome="proven",
            passed=True,
            reviewed=True,
            official_evaluation=True,
            perf_metric=104_257_741.0,
            perf_unit="total_ops_per_sec",
        ),
    )
    project.state.save_round(
        run_id,
        _round(
            2,
            commit="b" * 40,
            hypothesis_id="queue-mask-addressing",
            hypothesis_outcome="proven",
            hypothesis_parent_round=1,
            passed=True,
            reviewed=True,
            official_evaluation=True,
            perf_metric=97_028_091.0,
            perf_unit="total_ops_per_sec",
        ),
    )
    supervisor = RunSupervisor()
    supervisor.attach(project.state.log_directory(run_id), project=project, run_id=run_id)

    entries = SupervisionService(supervisor).execute(ExperimentQuery()).experiments

    by_id = {entry.hypothesis_id: entry for entry in entries}
    assert by_id["queue-mask-addressing"].resolved_outcome == "disproven"
    assert by_id["queue-mask-addressing"].kept is False


def test_service_returns_nothing_before_a_run_is_attached(tmp_path: Path) -> None:
    """Bootstrap is distinct from an attached run with an empty log."""
    supervisor = RunSupervisor()
    supervisor.attach(tmp_path / "logs")

    response = SupervisionService(supervisor).execute(ExperimentQuery())

    assert response.experiments == []
    assert response.experiments_ready is False


def test_service_returns_authoritative_empty_log_after_attach(tmp_path: Path) -> None:
    project, run_id = _project_run(tmp_path / "project")
    supervisor = RunSupervisor()
    supervisor.attach(project.state.log_directory(run_id), project=project, run_id=run_id)

    response = SupervisionService(supervisor).execute(ExperimentQuery())

    assert response.experiments == []
    assert response.experiments_ready is True


def test_service_returns_nothing_for_a_non_agent_outer_loop(tmp_path: Path) -> None:
    """Only the agent loop records hypotheses, so other loops have no log to show."""
    project, run_id = _project_run(tmp_path / "project", _plain_configuration())
    supervisor = RunSupervisor()
    supervisor.attach(project.state.log_directory(run_id), project=project, run_id=run_id)

    assert SupervisionService(supervisor).experiments() == []
