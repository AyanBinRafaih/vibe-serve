"""Server projection tests for the authoritative agent-run aggregate."""

from __future__ import annotations

from typing import TYPE_CHECKING

from vibesys.loops.agent.model import (
    AgentRunState,
    Hypothesis,
    HypothesisMeasurement,
    HypothesisResolution,
    HypothesisReview,
    HypothesisStrategy,
)
from vibesys.loops.agent.state import AgentRunStateStore
from vibesys.schemas import OrchestratorPlan
from vibesys.server import RunSupervisor
from vibesys.server.experiments import build_experiment_log
from vibesys.server.protocol import ExperimentQuery, PerformanceQuery
from vibesys.server.service import SupervisionService
from vs_loop_state import RoundRecord
from vs_project import AgentRunConfiguration, Project, RunEnvironmentRecord

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


def _hypothesis(identifier: str, started_round: int, **overrides: object) -> Hypothesis:
    fields: dict[str, object] = {
        "hypothesis_id": identifier,
        "plan": OrchestratorPlan(
            hypothesis_id=identifier,
            hypothesis=f"claim for {identifier}",
            task=f"test {identifier}",
            pass_criteria="",
            reasoning="",
        ),
        "started_round": started_round,
    }
    fields.update(overrides)
    return Hypothesis(**fields)  # type: ignore[arg-type]


def test_projection_uses_nested_rounds_and_one_official_measurement_tuple() -> None:
    hypothesis = _hypothesis(
        "H-01",
        1,
        rounds=[
            _round(1, hypothesis_id="H-01", perf_metric=100.0, perf_unit="ops_s"),
            _round(2, hypothesis_id="H-01", perf_metric=125.0, perf_unit="ops_s"),
        ],
        review=HypothesisReview.PASS,
        resolution=HypothesisResolution.DISPROVEN,
        measurement=HypothesisMeasurement(
            round=1,
            metric="throughput",
            value=100.0,
            unit="ops_s",
            direction="max",
            baseline_value=110.0,
            delta_pct=-9.09,
        ),
        candidate_retained=False,
        strategy=HypothesisStrategy.ABANDONED,
        strategy_reason="The official baseline regressed.",
    )

    (entry,) = build_experiment_log(AgentRunState(hypotheses=[hypothesis]))

    assert [round_.round for round_ in entry.rounds] == [1, 2]
    assert (entry.first_round, entry.last_round) == (1, 2)
    assert entry.resolved_outcome == "disproven"
    assert entry.judge_verdict == "pass"
    assert entry.kept is False
    assert entry.strategy_disposition == "abandoned"
    # Do not combine the newer second-round value with the official first-round
    # causal comparison.
    assert (entry.perf_metric, entry.perf_unit, entry.perf_delta_pct) == (
        100.0,
        "ops_s",
        -9.09,
    )


def test_projection_surfaces_active_hypothesis_before_a_round_finishes() -> None:
    state = AgentRunState(
        active_hypothesis_id="H-02",
        hypotheses=[_hypothesis("H-02", 2)],
    )

    (entry,) = build_experiment_log(state)

    assert entry.active is True
    assert entry.rounds == []
    assert (entry.first_round, entry.last_round) == (2, 2)
    assert entry.claim == "claim for H-02"


def test_projection_orders_hypotheses_by_started_round() -> None:
    state = AgentRunState(
        hypotheses=[
            _hypothesis("H-B", 3, rounds=[_round(3, hypothesis_id="H-B")]),
            _hypothesis("H-A", 1, rounds=[_round(1, hypothesis_id="H-A")]),
        ]
    )

    entries = build_experiment_log(state)

    assert [entry.hypothesis_id for entry in entries] == ["H-A", "H-B"]


def _configuration() -> AgentRunConfiguration:
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


def _project_run(project: Path) -> tuple[Project, str]:
    project.mkdir()
    (project / "OBJECTIVE.md").write_text("Make the queue fast.\n", encoding="utf-8")
    vibesys_project = Project.open(project)
    vibesys_project.state.create_project("queue")
    manifest = vibesys_project.state.new_run_manifest(
        "queue",
        run_id="queue-run",
        branch="vibesys/queue-run",
        vibesys_version="0.2.0-test",
        configuration=_configuration(),
        trusted_input_baseline="0" * 40,
    )
    vibesys_project.state.create_run(manifest)
    return vibesys_project, manifest.run_id


def test_service_reads_only_authoritative_agent_state(tmp_path: Path) -> None:
    project, run_id = _project_run(tmp_path / "project")
    portable = project.state.portable_namespace(run_id, "agent")
    AgentRunStateStore(portable).save(
        AgentRunState(
            active_hypothesis_id="H-02",
            hypotheses=[
                _hypothesis("H-01", 1, rounds=[_round(1, hypothesis_id="H-01")]),
                _hypothesis("H-02", 2),
            ],
        )
    )
    supervisor = RunSupervisor()
    supervisor.attach(project.state.log_directory(run_id), project=project, run_id=run_id)

    response = SupervisionService(supervisor).execute(ExperimentQuery())

    assert [entry.hypothesis_id for entry in response.experiments] == ["H-01", "H-02"]
    assert response.experiments[1].active is True
    assert response.experiments_ready is True


def test_service_reads_performance_from_authoritative_agent_state(tmp_path: Path) -> None:
    project, run_id = _project_run(tmp_path / "project")
    portable = project.state.portable_namespace(run_id, "agent")
    AgentRunStateStore(portable).save(
        AgentRunState(
            hypotheses=[
                _hypothesis(
                    "H-01",
                    1,
                    rounds=[
                        _round(
                            1,
                            hypothesis_id="H-01",
                            perf_metric=42.0,
                            perf_unit="ops_s",
                            passed=True,
                        )
                    ],
                )
            ]
        )
    )
    supervisor = RunSupervisor()
    supervisor.attach(project.state.log_directory(run_id), project=project, run_id=run_id)

    response = SupervisionService(supervisor).execute(PerformanceQuery())

    assert [(item.round, item.perf_metric) for item in response.performance] == [(1, 42.0)]


def test_service_adapts_legacy_state_read_only(tmp_path: Path) -> None:
    project, run_id = _project_run(tmp_path / "project")
    project.state.save_round(
        run_id,
        _round(
            1,
            hypothesis_id="H-01",
            hypothesis_claim="legacy claim",
            hypothesis_task="legacy task",
        ),
    )
    portable = project.state.portable_namespace(run_id, "agent")
    store = AgentRunStateStore(portable)
    supervisor = RunSupervisor()
    supervisor.attach(project.state.log_directory(run_id), project=project, run_id=run_id)

    (entry,) = SupervisionService(supervisor).execute(ExperimentQuery()).experiments

    assert entry.hypothesis_id == "H-01"
    assert entry.claim == "legacy claim"
    assert store.load_optional() is None


def test_service_returns_authoritative_empty_log_after_attach(tmp_path: Path) -> None:
    project, run_id = _project_run(tmp_path / "project")
    supervisor = RunSupervisor()
    supervisor.attach(project.state.log_directory(run_id), project=project, run_id=run_id)

    response = SupervisionService(supervisor).execute(ExperimentQuery())

    assert response.experiments == []
    assert response.experiments_ready is True
