"""Server projection tests for the per-round design log."""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING, Literal, TypedDict, Unpack

from tests.server.support import build_server_parts

from server.api.design import build_design_log
from server.api.protocol import DesignQuery
from vibesys.loops.agent.model import AgentRunState, Hypothesis
from vibesys.loops.agent.state import AgentRunStateStore
from vibesys.schemas import OrchestratorPlan
from vs_loop_state import RoundRecord
from vs_project import AgentRunConfiguration, Project, RunEnvironmentRecord

if TYPE_CHECKING:
    from pathlib import Path


class _RoundFields(TypedDict, total=False):
    """Keyword fields of ``RoundRecord``, so helper overrides stay checked."""

    round_number: int
    commit: str | None
    perf_metric: float | None
    perf_unit: str | None
    passed: bool
    hypothesis_id: str | None
    judge_verdict: Literal["pass", "fail", "deferred"] | None
    hypothesis_outcome: str | None
    hypothesis_claim: str | None
    hypothesis_task: str | None
    official_evaluation: bool
    candidate_disposition: str
    perf_delta_pct: float | None


class _HypothesisFields(TypedDict, total=False):
    """Keyword fields of ``Hypothesis``, so helper overrides stay checked."""

    hypothesis_id: str
    plan: OrchestratorPlan
    started_round: int
    parent_round: int | None
    parent_commit: str | None
    rounds: list[RoundRecord]


def _round(number: int, **overrides: Unpack[_RoundFields]) -> RoundRecord:
    fields: _RoundFields = {
        "round_number": number,
        "commit": f"c{number}",
        "perf_metric": None,
        "perf_unit": None,
        "passed": False,
    }
    fields.update(overrides)
    return RoundRecord(**fields)


def _hypothesis(
    identifier: str, started_round: int, /, **overrides: Unpack[_HypothesisFields]
) -> Hypothesis:
    fields: _HypothesisFields = {
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
    return Hypothesis(**fields)


def _git(workspace: Path, *args: str) -> str:
    command = ["git", "-C", str(workspace), *args]
    result = subprocess.run(command, capture_output=True, check=True, text=True)  # noqa: S603
    return result.stdout.strip()


def _repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "--quiet")
    _git(path, "config", "user.name", "Test")
    _git(path, "config", "user.email", "test@example.invalid")
    _git(path, "config", "commit.gpgsign", "false")
    return path


def _commit_all(path: Path, message: str) -> str:
    _git(path, "add", "-A")
    _git(path, "commit", "--quiet", "-m", message)
    return _git(path, "rev-parse", "HEAD")


def test_build_design_log_derives_per_round_file_changes(tmp_path: Path) -> None:
    workspace = _repo(tmp_path / "workspace")
    (workspace / "src").mkdir()
    (workspace / "src" / "lib.rs").write_text("fn main() {}\n", encoding="utf-8")
    baseline = _commit_all(workspace, "baseline")

    (workspace / "src" / "lib.rs").write_text("fn main() { fast() }\n", encoding="utf-8")
    (workspace / "src" / "ffi.rs").write_text("pub fn fast() {}\n", encoding="utf-8")
    (workspace / ".vibesys" / "state").mkdir(parents=True)
    (workspace / ".vibesys" / "state" / "note").write_text("bookkeeping\n", encoding="utf-8")
    (workspace / "progress.md").write_text("round 1\n", encoding="utf-8")
    (workspace / "pareto-frontier.md").write_text("# Pareto frontier\n", encoding="utf-8")
    first = _commit_all(workspace, "round 1")

    (workspace / "src" / "ffi.rs").unlink()
    (workspace / "src" / "lib.rs").rename(workspace / "src" / "queue.rs")
    second = _commit_all(workspace, "round 2")

    state = AgentRunState(
        hypotheses=[
            _hypothesis(
                "H-01",
                1,
                parent_commit=baseline,
                rounds=[
                    _round(1, hypothesis_id="H-01", commit=first),
                    _round(2, hypothesis_id="H-01", commit=second),
                ],
            )
        ]
    )

    first_entry, second_entry = build_design_log(state, workspace=workspace, baseline=baseline)

    assert first_entry.files is not None
    assert sorted((change.change, change.path) for change in first_entry.files) == [
        ("added", "src/ffi.rs"),
        ("modified", "src/lib.rs"),
    ]
    assert second_entry.files is not None
    assert [(change.change, change.path, change.renamed_from) for change in second_entry.files] == [
        ("deleted", "src/ffi.rs", None),
        ("renamed", "src/queue.rs", "src/lib.rs"),
    ]


def test_build_design_log_measures_a_reverted_hypothesis_from_its_parent(tmp_path: Path) -> None:
    workspace = _repo(tmp_path / "workspace")
    (workspace / "queue.rs").write_text("original\n", encoding="utf-8")
    baseline = _commit_all(workspace, "baseline")

    (workspace / "queue.rs").write_text("attempt one\n", encoding="utf-8")
    first = _commit_all(workspace, "round 1")

    _git(workspace, "checkout", "--quiet", baseline, "--", "queue.rs")
    (workspace / "batching.rs").write_text("attempt two\n", encoding="utf-8")
    second = _commit_all(workspace, "round 2 from baseline")

    state = AgentRunState(
        hypotheses=[
            _hypothesis(
                "H-01",
                1,
                parent_commit=baseline,
                rounds=[_round(1, hypothesis_id="H-01", commit=first)],
            ),
            _hypothesis(
                "H-02",
                2,
                parent_commit=baseline,
                rounds=[_round(2, hypothesis_id="H-02", commit=second)],
            ),
        ]
    )

    _, second_entry = build_design_log(state, workspace=workspace, baseline=baseline)

    # Against round 1 the range would also claim queue.rs reverted; against
    # the hypothesis's own parent it is exactly the new file.
    assert second_entry.files is not None
    assert [(change.change, change.path) for change in second_entry.files] == [
        ("added", "batching.rs")
    ]


def test_build_design_log_leaves_unresolvable_ranges_unknown(tmp_path: Path) -> None:
    state = AgentRunState(
        hypotheses=[
            _hypothesis(
                "H-01",
                1,
                rounds=[
                    _round(1, hypothesis_id="H-01", commit=None),
                    _round(2, hypothesis_id="H-01", commit="a" * 40),
                ],
            )
        ]
    )

    entries = build_design_log(state, workspace=tmp_path, baseline="0" * 40)

    # Round 1 recorded no checkpoint; round 2's range does not resolve in a
    # directory with no git history. Both stay None, never [].
    assert [entry.files for entry in entries] == [None, None]


def test_build_design_log_copies_stage_fields_and_titles(tmp_path: Path) -> None:
    state = AgentRunState(
        hypotheses=[
            _hypothesis(
                "H-01",
                1,
                plan=OrchestratorPlan(
                    hypothesis_id="H-01",
                    title="Batch decode requests",
                    hypothesis="claim for H-01",
                    task="test H-01",
                    pass_criteria="",
                    reasoning="",
                ),
                rounds=[
                    _round(
                        1,
                        hypothesis_id="H-01",
                        hypothesis_claim="claim for H-01",
                        hypothesis_task="test H-01",
                        hypothesis_outcome="proven",
                        judge_verdict="pass",
                        passed=True,
                        official_evaluation=True,
                        candidate_disposition="retained",
                        perf_metric=125.0,
                        perf_unit="ops_s",
                        perf_delta_pct=25.0,
                    )
                ],
            )
        ]
    )

    (entry,) = build_design_log(state, workspace=tmp_path, baseline="0" * 40)

    assert entry.round == 1
    assert entry.hypothesis_id == "H-01"
    assert entry.title == "Batch decode requests"
    assert entry.claim == "claim for H-01"
    assert entry.task == "test H-01"
    assert entry.hypothesis_outcome == "proven"
    assert entry.judge_verdict == "pass"
    assert entry.passed is True
    assert entry.official_evaluation is True
    assert entry.candidate_disposition == "retained"
    assert (entry.perf_metric, entry.perf_unit, entry.perf_delta_pct) == (125.0, "ops_s", 25.0)


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


def _project_run(project: Path, *, trusted_input_baseline: str) -> tuple[Project, str]:
    vibesys_project = Project.open(project)
    vibesys_project.state.create_project("queue")
    manifest = vibesys_project.state.new_run_manifest(
        "queue",
        run_id="queue-run",
        branch="vibesys/queue-run",
        vibesys_version="0.2.0-test",
        configuration=_configuration(),
        trusted_input_baseline=trusted_input_baseline,
    )
    vibesys_project.state.create_run(manifest)
    return vibesys_project, manifest.run_id


def test_service_builds_design_from_workspace_history(tmp_path: Path) -> None:
    workspace = _repo(tmp_path / "project")
    (workspace / "OBJECTIVE.md").write_text("Make the queue fast.\n", encoding="utf-8")
    baseline = _commit_all(workspace, "baseline")
    (workspace / "ring.rs").write_text("ring buffer\n", encoding="utf-8")
    first = _commit_all(workspace, "round 1")

    project, run_id = _project_run(workspace, trusted_input_baseline=baseline)
    portable = project.state.portable_namespace(run_id, "agent")
    AgentRunStateStore(portable).save(
        AgentRunState(
            hypotheses=[
                _hypothesis(
                    "H-01",
                    1,
                    rounds=[_round(1, hypothesis_id="H-01", commit=first, passed=True)],
                )
            ]
        )
    )
    parts = build_server_parts(project.state.log_directory(run_id), project=project, run_id=run_id)

    response = parts.api.execute(DesignQuery())

    assert response.design_ready is True
    (entry,) = response.design
    assert entry.round == 1
    assert entry.commit == first
    assert entry.files is not None
    assert [(change.change, change.path) for change in entry.files] == [("added", "ring.rs")]


def test_service_reports_design_not_ready_before_attach(tmp_path: Path) -> None:
    parts = build_server_parts(tmp_path / "logs")

    response = parts.api.execute(DesignQuery())

    assert response.design == []
    assert response.design_ready is False
