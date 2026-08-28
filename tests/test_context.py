import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from vibesys import boot_trace
from vibesys.context import (
    _EXPERIMENT_CHAT_SYSTEM_PROMPT,
    _ExperimentChatDependencies,
    _ExperimentChatService,
    _resolve_chat_thread_settings,
    _resume_configuration_update,
    _RunContext,
    create_candidate_context,
    create_run_context,
)
from vibesys.domains.base import DomainName
from vibesys.domains.environment import EnvironmentPatch, NoopEnvironmentHooks
from vibesys.domains.llm_serving.hooks import LLMServingEnvironmentHooks
from vibesys.errors import ConfigurationError
from vibesys.input_manifest import WorkspaceSource
from vibesys.loops.agent.model import AgentRunState
from vibesys.profilers import ProfilerKind, ProfilerPreflightResult
from vibesys.run import RunLogger, RunPaths, RunStateNamespace
from vibesys.sandbox.run_environment import RunEnvironmentSpec
from vibesys.server import EventType, RunSupervisor
from vibesys.server.registry import REGISTRY
from vs_loop_state import PlainLoopCursor
from vs_project import AgentRunConfiguration, Project, RunEnvironmentRecord


class _FakeBackend:
    image = "fake-image"
    selected_device = None

    def __init__(self) -> None:
        self.sandbox = MagicMock()

    def make_sandbox(self, *_args, **_kwargs):  # noqa: ANN002, ANN003, ANN202
        return self.sandbox

    def make_monitor(self, _log_dir):  # noqa: ANN001, ANN202
        return None


class _RecordingHooks:
    def __init__(self) -> None:
        self.prepared = 0
        self.torn_down = 0

    def prepare(self, _ctx):  # noqa: ANN001, ANN202
        self.prepared += 1
        return EnvironmentPatch()

    def teardown(self, _ctx):  # noqa: ANN001, ANN202
        self.torn_down += 1


@pytest.fixture(autouse=True)
def _context_dependencies(monkeypatch):  # pyright: ignore[reportUnusedFunction]  # noqa: ANN001, ANN202
    monkeypatch.setattr("vibesys.context.backends.get", lambda *_args, **_kwargs: _FakeBackend())
    monkeypatch.setattr("vibesys.context.build_agent_client", lambda *_args, **_kwargs: MagicMock())
    monkeypatch.setattr(
        "vibesys.context.preflight_profiler_kind",
        lambda kind: ProfilerPreflightResult(kind, True),  # noqa: FBT003
    )


def _configuration(max_rounds: int = 1) -> AgentRunConfiguration:
    return AgentRunConfiguration(
        outer_loop="agent",
        run_environment=RunEnvironmentRecord(name="local"),
        inner_loop="multi-agent",
        interface="inprocess",
        model="gpt-test",
        agent_backend="stub",
        compute_backend="cpu",
        profiler="none",
        max_rounds=max_rounds,
        max_retries_per_round=1,
        judge_every=1,
        official_eval_every=1,
        memory_layout="files",
    )


def test_resume_adopts_objectives_omitted_by_legacy_agent_manifest() -> None:
    requested = _configuration().model_copy(update={"objectives": ("throughput:max",)})
    legacy_payload = requested.model_dump(exclude={"objectives"})
    recorded = AgentRunConfiguration.model_validate(legacy_payload)

    assert "objectives" not in recorded.model_fields_set
    assert _resume_configuration_update(recorded, requested) == requested


def _write_project(root: Path, *, evaluator_name: str = "checker") -> Path:
    root.mkdir()
    (root / "OBJECTIVE.md").write_text("Make the queue faster.\n")
    evaluator = root / evaluator_name
    evaluator.mkdir()
    (evaluator / "check.py").write_text("print('ok')\n")
    (root / "queue.py").write_text("VALUE = 1\n")
    (root / "vibesys.input.toml").write_text(
        """\
version = 1

[agent]
domain = "generic"

[accuracy]
command = ["python", "_evaluator/checker/check.py"]

[benchmark]
command = ["python", "_evaluator/checker/check.py"]

[evaluator]
source = "checker"
"""
    )
    return evaluator


def _write_serving_task(root: Path, name: str = "latency") -> Path:
    task = root / ".vibesys" / "tasks" / name
    reference = task / "reference"
    reference.mkdir(parents=True)
    (task / "OBJECTIVE.md").write_text("Reduce latency.\n", encoding="utf-8")
    (task / "vibesys.input.toml").write_text(
        """\
version = 1

[agent]
domain = "llm-serving"

[accuracy]
command = ["python", "checker.py"]

[benchmark]
command = ["python", "benchmark.py"]
""",
        encoding="utf-8",
    )
    (reference / "meta.json").write_text(
        '{"model_id": "org/model", "revision": "abc"}',
        encoding="utf-8",
    )
    return task


def _create_context(  # noqa: PLR0913
    project: Path,
    *,
    runs_dir: Path | None = None,
    evaluator: Path | None = None,
    exp_name: str = "queue",
    existing: bool = False,
    configuration: AgentRunConfiguration | None = None,
    objective: str = "Make the queue faster.\n",
    task_name: str | None = None,
    task_root: Path | None = None,
    remote_repo: str | None = None,
    hooks=None,  # noqa: ANN001
) -> _RunContext:
    return create_run_context(
        config={"model": {"name": "gpt-test"}},  # pyright: ignore[reportArgumentType]
        exp_name=exp_name,
        runs_dir=runs_dir,
        input_path=str(project),
        accuracy_command="python _evaluator/checker/check.py",
        benchmark_command="python _evaluator/checker/check.py",
        task_name=task_name,
        task_root=task_root,
        evaluator_path=evaluator,
        objective=objective,
        existing=existing,
        project_configuration=configuration or _configuration(),
        profiler_kind=ProfilerKind.NONE,
        profiler_domain=DomainName.GENERIC,
        run_environment=RunEnvironmentSpec("local"),
        agent_backend="stub",
        environment_hooks=hooks or NoopEnvironmentHooks(),
        remote_repo=remote_repo,
        agent_state_model_type=AgentRunState,
    )


def _git(project: Path, *args: str) -> str:
    return subprocess.run(  # noqa: S603
        [  # noqa: S607
            "git",
            "-c",
            "user.name=VibeSys Test",
            "-c",
            "user.email=test@vibesys.invalid",
            *args,
        ],
        cwd=project,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_direct_run_uses_one_project_root_and_canonical_state(tmp_path):  # noqa: ANN001, ANN201
    project = tmp_path / "queue"
    evaluator = _write_project(project)
    runner = MagicMock()

    with patch("vibesys.context.build_agent_client", return_value=runner) as build_runner:
        with _create_context(project, evaluator=evaluator) as ctx:
            assert ctx.project_root == project
            assert ctx.workspace == project
            assert ctx.project.root == project
            assert ctx.log_dir == ctx.project.state.log_directory(ctx.run_id)
            assert (
                not ctx.state.local(RunStateNamespace.AGENT)
                .external_directory()
                .is_relative_to(project)
            )
            objective_path = Path(ctx.objective_location)
            assert objective_path == (
                ctx.project.state.portable_namespace(ctx.run_id, "runtime").external_directory()
                / "effective-objective.md"
            )
            assert objective_path.read_text() == "Make the queue faster.\n"
            assert objective_path.is_relative_to(ctx.workspace)

        policy = build_runner.call_args.kwargs["project_path_policy"]
        state_paths = Project.open(project).state.sandbox_paths()
        assert state_paths.read_only_path in policy.read_only_paths
        assert state_paths.hidden_path is None
        runner.close.assert_called_once_with()

    manifest = Project.open(project).state.load_run(ctx.run_id)
    assert manifest.branch == f"vibesys-runs/{ctx.run_id}"
    assert _git(project, "branch", "--show-current") == manifest.branch
    assert _git(project, "status", "--porcelain") == ""


def test_run_context_announces_canonical_experiment_state(tmp_path):  # noqa: ANN001, ANN201
    project = tmp_path / "queue"
    evaluator = _write_project(project)
    supervisor = RunSupervisor()
    supervisor.attach(tmp_path / "bootstrap")
    REGISTRY.activate(supervisor)
    try:
        with _create_context(project, evaluator=evaluator) as ctx:
            changed = [
                event
                for event in supervisor.read_events()
                if event.type is EventType.EXPERIMENTS_CHANGED
            ]

            assert supervisor.project_run is not None
            assert supervisor.project_run.run_id == ctx.run_id
            assert len(changed) == 1
            assert changed[0].data is not None
            assert changed[0].data.kind == "experiments_changed"
            assert changed[0].data.reason == "project_attached"
    finally:
        REGISTRY.deactivate(supervisor)


def test_context_assembly_logs_stage_timings(tmp_path):  # noqa: ANN001, ANN201
    """Every assembly span up to and past the experiments gate reaches the run log.

    This is a regression guard for the diagnostic used to find where
    ``create_run_context`` spends time before the TUI's hypothesis screen
    can leave "loading experiments..." (the gate flips when the second
    ``supervisor.attach`` records ``EXPERIMENTS_CHANGED``).
    """
    project = tmp_path / "queue"
    evaluator = _write_project(project)
    supervisor = RunSupervisor()
    supervisor.attach(tmp_path / "bootstrap")
    REGISTRY.activate(supervisor)
    try:
        with _create_context(project, evaluator=evaluator) as ctx:
            log_text = ctx.run_log_path.read_text()
    finally:
        REGISTRY.deactivate(supervisor)

    for stage in (
        "config_and_inputs",
        "backend_and_model",
        "profiler_preflight",
        "workspace_materialize",
        "project_open",
        "log_bootstrap",
        "git_tracker_init",
        "project_state_resume",
        "round_transaction_recovery",
        "workspace_setup",
        "environment_open",
        "device_monitor_start",
        "agent_client_build",
    ):
        assert f"boot span context.{stage}: " in log_text, f"missing span timing for {stage!r}"
    # The enclosing span is assembly's total, recorded after its children.
    assert "boot span context: " in log_text
    assert "experiments gate open after " in log_text


def test_dispatch_preamble_spans_reach_run_log(tmp_path):  # noqa: ANN001, ANN201
    """Spans closed before ``create_run_context`` land in the run log, first.

    ``_dispatch`` and ``_run_agent`` (main.py) do substantial work before a
    ``RunLogger`` exists and record ``boot_trace`` spans as they go.
    ``_assemble_run_context`` must drain that buffer at entry, so the
    preamble's spans reach the persistent run log ahead of assembly's own.
    """
    project = tmp_path / "queue"
    evaluator = _write_project(project)
    supervisor = RunSupervisor()
    supervisor.attach(tmp_path / "bootstrap")
    REGISTRY.activate(supervisor)

    boot_trace.drain_log_lines()
    with boot_trace.span("agent_preamble"), boot_trace.span("load_config_and_skills"):
        pass
    try:
        with _create_context(project, evaluator=evaluator) as ctx:
            log_text = ctx.run_log_path.read_text()
    finally:
        REGISTRY.deactivate(supervisor)

    assert "boot span agent_preamble.load_config_and_skills: " in log_text
    assert "boot span agent_preamble: " in log_text
    # The preamble happened before assembly in real dispatch; the run log
    # should preserve that order.
    preamble_index = log_text.index("boot span agent_preamble: ")
    context_index = log_text.index("boot span context.config_and_inputs: ")
    assert preamble_index < context_index


def test_context_assembly_without_recorded_preamble_omits_preamble_lines(tmp_path):  # noqa: ANN001, ANN201
    """No preamble spans (e.g. a test-built context) means no stray lines."""
    project = tmp_path / "queue"
    evaluator = _write_project(project)
    supervisor = RunSupervisor()
    supervisor.attach(tmp_path / "bootstrap")
    REGISTRY.activate(supervisor)
    boot_trace.drain_log_lines()
    try:
        with _create_context(project, evaluator=evaluator) as ctx:
            log_text = ctx.run_log_path.read_text()
    finally:
        REGISTRY.deactivate(supervisor)

    assert "boot span agent_preamble" not in log_text
    assert "boot span dispatch" not in log_text


def test_context_assembly_spans_stay_off_stderr_by_default(tmp_path, capfd):  # noqa: ANN001, ANN201
    """Boot spans are forensics in the run log, not narration at the operator."""
    project = tmp_path / "queue"
    evaluator = _write_project(project)
    supervisor = RunSupervisor()
    supervisor.attach(tmp_path / "bootstrap")
    REGISTRY.activate(supervisor)
    try:
        with _create_context(project, evaluator=evaluator) as ctx:
            log_text = ctx.run_log_path.read_text()
            captured_err = capfd.readouterr().err
    finally:
        REGISTRY.deactivate(supervisor)

    assert "boot span context: " in log_text
    assert "boot span" not in captured_err


def test_boot_trace_env_puts_assembly_spans_on_stderr(tmp_path, capfd, monkeypatch):  # noqa: ANN001, ANN201
    project = tmp_path / "queue"
    evaluator = _write_project(project)
    supervisor = RunSupervisor()
    supervisor.attach(tmp_path / "bootstrap")
    REGISTRY.activate(supervisor)
    monkeypatch.setenv(boot_trace.BOOT_TRACE_ENV, "1")
    try:
        with _create_context(project, evaluator=evaluator) as ctx:
            assert "boot span context: " in ctx.run_log_path.read_text()
            captured_err = capfd.readouterr().err
    finally:
        REGISTRY.deactivate(supervisor)

    assert "boot span context.config_and_inputs: " in captured_err


def test_retained_experiment_chat_uses_one_dedicated_client_across_run_teardown(
    tmp_path: Path,
) -> None:
    project = tmp_path / "queue"
    evaluator = _write_project(project)
    supervisor = RunSupervisor()
    supervisor.attach(tmp_path / "bootstrap")
    supervisor.enable_terminal_chat_retention()
    primary_client = MagicMock()
    chat_client = MagicMock()
    chat_client.invoke_text.side_effect = ["live answer", "terminal answer"]
    REGISTRY.activate(supervisor)
    try:
        with patch(
            "vibesys.context.build_agent_client",
            side_effect=[primary_client, chat_client],
        ) as build_client:
            ctx = _create_context(project, evaluator=evaluator)
            assert build_client.call_count == 2
            assert "chat" not in build_client.call_args_list[0].kwargs["backends"]
            assert supervisor.chat("what is happening?") == "live answer"

            ctx.close()

            primary_client.close.assert_called_once_with()
            chat_client.close.assert_not_called()
            assert supervisor.chat("what happened?") == "terminal answer"
            assert chat_client.invoke_text.call_count == 2

        supervisor.close_terminal_chat_resource()
        chat_client.close.assert_called_once_with()
    finally:
        supervisor.close_terminal_chat_resource()
        REGISTRY.deactivate(supervisor)


def test_nonretained_experiment_chat_closes_when_context_construction_fails(
    tmp_path: Path,
) -> None:
    project = tmp_path / "queue"
    evaluator = _write_project(project)
    supervisor = RunSupervisor()
    supervisor.attach(tmp_path / "bootstrap")
    primary_client = MagicMock()
    chat_client = MagicMock()
    construction_error = RuntimeError("context construction failed")
    REGISTRY.activate(supervisor)
    try:
        with (
            patch(
                "vibesys.context.build_agent_client",
                side_effect=[primary_client, chat_client],
            ),
            patch("vibesys.context._RunContext", side_effect=construction_error),
            pytest.raises(RuntimeError) as raised,
        ):
            _create_context(project, evaluator=evaluator)

        assert raised.value is construction_error
        primary_client.close.assert_called_once_with()
        chat_client.close.assert_called_once_with()
    finally:
        REGISTRY.deactivate(supervisor)


def test_chat_thread_settings_resolve_run_defaults() -> None:
    resolved = _resolve_chat_thread_settings(
        agent_backend="cli",
        default_driver="agentshim",
        default_provider="codex",
        default_model="gpt-run",
        driver=None,
        provider=None,
        model=None,
    )
    assert resolved == ("agentshim", "codex", "gpt-run")


def test_chat_thread_settings_reject_invalid_combinations() -> None:
    def resolve(agent_backend: str, driver: str | None, provider: str | None) -> tuple[str, ...]:
        return _resolve_chat_thread_settings(
            agent_backend=agent_backend,
            default_driver="agentshim",
            default_provider="codex",
            default_model="gpt-run",
            driver=driver,
            provider=provider,
            model=None,
        )

    with pytest.raises(ValueError, match="does not support provider 'gemini'"):
        resolve("cli", "omnigent", "gemini")
    with pytest.raises(ValueError, match="unknown agent driver 'other'"):
        resolve("cli", "other", None)
    with pytest.raises(ValueError, match="require the CLI agent backend"):
        resolve("deepagents", None, None)


def _chat_service(
    tmp_path: Path, thread_id: str | None, answer: str = "the answer"
) -> tuple[_ExperimentChatService, MagicMock]:
    supervisor = RunSupervisor()
    supervisor.attach(tmp_path / "logs")
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    agent_client = MagicMock()
    agent_client.invoke_text.return_value = answer
    project = MagicMock()
    project.state.portable_run_export.return_value = MagicMock(files=[])
    service = _ExperimentChatService(
        _ExperimentChatDependencies(
            supervisor=supervisor,
            agent_client=agent_client,
            workspace=workspace,
            log_dir=tmp_path / "logs",
            project=project,
            run_id="run-1",
            log=lambda _line: None,
            flush_logs=lambda: None,
            environment=dict,
            progress=lambda: None,
            driver="agentshim",
            provider="codex",
            model="gpt-test",
            thread_id=thread_id,
        )
    )
    return service, agent_client


def test_default_chat_thread_keeps_the_legacy_state_layout(tmp_path: Path) -> None:
    service, agent_client = _chat_service(tmp_path, thread_id=None)

    service.ask("what happened?")

    workspace = tmp_path / "workspace"
    transcript = workspace / "_vibesys_chat" / "conversation.jsonl"
    assert json.loads(transcript.read_text()) == {
        "question": "what happened?",
        "answer": "the answer",
    }
    prompt = agent_client.invoke_text.call_args.kwargs["system_prompt"]
    assert prompt == _EXPERIMENT_CHAT_SYSTEM_PROMPT
    assert (workspace / "_vibesys_chat" / "instructions.md").read_text() == prompt


def test_created_chat_thread_owns_its_state_dir_and_shares_trajectory(tmp_path: Path) -> None:
    service, agent_client = _chat_service(tmp_path, thread_id="thread-a")

    service.ask("what happened?")

    workspace = tmp_path / "workspace"
    thread_dir = workspace / "_vibesys_chat" / "threads" / "thread-a"
    assert json.loads((thread_dir / "conversation.jsonl").read_text()) == {
        "question": "what happened?",
        "answer": "the answer",
    }
    prompt = agent_client.invoke_text.call_args.kwargs["system_prompt"]
    # The thread reads its own conversation but the shared trajectory.
    assert "_vibesys_chat/threads/thread-a/conversation.jsonl" in prompt
    assert "_vibesys_chat/trajectory/state/" in prompt
    assert (thread_dir / "instructions.md").read_text() == prompt
    assert (workspace / "_vibesys_chat" / "trajectory").is_dir()
    assert not (thread_dir / "trajectory").exists()
    # The legacy transcript is untouched by thread traffic.
    assert not (workspace / "_vibesys_chat" / "conversation.jsonl").exists()

    # A follow-up turn continues from the thread's own instructions file.
    service.ask("and then?")
    continuation = agent_client.invoke_text.call_args.kwargs["system_prompt"]
    assert "_vibesys_chat/threads/thread-a/instructions.md" in continuation


def test_chat_execution_events_carry_the_threads_runtime_identity(tmp_path: Path) -> None:
    """A chat execution is labelled like a round agent's, not as an anonymous chat."""
    service, _ = _chat_service(tmp_path, thread_id="thread-a")

    service.ask("what happened?")

    recorded = [
        json.loads(line)
        for line in (tmp_path / "logs" / "run-events.jsonl").read_text().splitlines()
        if line
    ]
    started = [
        event for event in recorded if event["type"] == EventType.AGENT_EXECUTION_STARTED.value
    ]
    assert len(started) == 1
    assert started[0]["agent_kind"] == "chat"
    assert started[0]["data"]["driver"] == "agentshim"
    assert started[0]["data"]["provider"] == "codex"
    assert started[0]["data"]["model"] == "gpt-test"


def test_repository_task_exposes_its_actual_reference_path(tmp_path: Path) -> None:
    project = tmp_path / "queue"
    evaluator = _write_project(project)
    task = project / ".vibesys" / "tasks" / "latency"
    reference = task / "reference"
    reference.mkdir(parents=True)
    (task / "OBJECTIVE.md").write_text("Reduce latency.\n", encoding="utf-8")
    (task / "vibesys.input.toml").write_text("version = 1\n", encoding="utf-8")
    (reference / "baseline.py").write_text("VALUE = 1\n", encoding="utf-8")

    with _create_context(
        project,
        evaluator=evaluator,
        task_name="latency",
        task_root=task,
    ) as ctx:
        assert ctx.ref_name == ".vibesys/tasks/latency/reference/baseline.py"


def test_copied_repository_task_materializes_model_outside_authored_inputs(
    tmp_path: Path,
) -> None:
    project = tmp_path / "serving"
    _write_project(project)
    task = _write_serving_task(project)
    reference = task / "reference"
    downloaded = tmp_path / "downloaded"
    downloaded.mkdir()
    runs_dir = tmp_path / "runs"

    with patch("huggingface_hub.snapshot_download", return_value=str(downloaded)):
        with _create_context(
            project,
            runs_dir=runs_dir,
            task_name="latency",
            task_root=task,
            hooks=LLMServingEnvironmentHooks(),
        ) as ctx:
            runtime_model = runs_dir / ".cache" / "llm-serving" / ctx.run_id / "model"
            copied_reference = ctx.project_root / ".vibesys" / "tasks" / "latency" / "reference"

            assert not (reference / "model").exists()
            assert not (copied_reference / "model").exists()
            assert runtime_model.resolve() == downloaded
            assert ctx.trusted_input_changes() == []

        assert _git(ctx.project_root, "status", "--porcelain") == ""


def test_direct_repository_task_materializes_model_in_local_state(tmp_path: Path) -> None:
    project = tmp_path / "serving"
    evaluator = _write_project(project)
    task = _write_serving_task(project)
    reference = task / "reference"
    downloaded = tmp_path / "downloaded"
    downloaded.mkdir()

    with patch("huggingface_hub.snapshot_download", return_value=str(downloaded)):
        with _create_context(
            project,
            evaluator=evaluator,
            task_name="latency",
            task_root=task,
            hooks=LLMServingEnvironmentHooks(),
        ) as ctx:
            runtime_model = ctx.project.state.model_cache_directory("llm-serving") / "model"

            assert not (reference / "model").exists()
            assert runtime_model.resolve() == downloaded
            assert ctx.trusted_input_changes() == []

        assert _git(project, "status", "--porcelain") == ""


def test_copied_run_provisions_self_contained_project_in_collection(tmp_path):  # noqa: ANN001, ANN201
    source = tmp_path / "input"
    evaluator = _write_project(source)
    runs_dir = tmp_path / "runs"

    with _create_context(source, runs_dir=runs_dir, evaluator=evaluator) as ctx:
        project = ctx.project_root
        assert project.parent == runs_dir
        assert project.name == ctx.run_id
        assert ctx.workspace == project
        assert (project / "queue.py").is_file()
        assert not (project / "checker").exists()
        assert (project / "_evaluator" / "checker" / "check.py").is_file()
        manifest_text = (project / "vibesys.input.toml").read_text()
        assert 'source = "_evaluator/checker"' in manifest_text
        assert "[workspace]" not in manifest_text
        assert ctx.log_dir == ctx.project.state.log_directory(ctx.run_id)

    assert _git(project, "status", "--porcelain") == ""


def test_resume_reuses_project_and_run_id_and_only_increases_limit(tmp_path):  # noqa: ANN001, ANN201
    project = tmp_path / "queue"
    evaluator = _write_project(project)
    with _create_context(project, evaluator=evaluator) as first:
        run_id = first.run_id

    with _create_context(
        project,
        evaluator=evaluator,
        exp_name=run_id,
        existing=True,
        configuration=_configuration(max_rounds=2),
    ) as resumed:
        assert resumed.project_root == project
        assert resumed.run_id == run_id

    stored = Project.open(project).state.load_run(run_id)
    assert stored.configuration.max_rounds == 2
    assert _git(project, "branch", "--show-current") == f"vibesys-runs/{run_id}"


def test_resume_migrates_legacy_objectives_with_dirty_candidate(tmp_path):  # noqa: ANN001, ANN201
    project = tmp_path / "queue"
    evaluator = _write_project(project)
    with _create_context(project, evaluator=evaluator) as first:
        run_id = first.run_id

    state = Project.open(project).state
    manifest_path = state._run_manifest_path(run_id)  # noqa: SLF001  # migration fixture
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["configuration"].pop("objectives")
    manifest_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    _git(project, "add", str(manifest_path.relative_to(project)))
    _git(project, "commit", "-m", "simulate legacy run manifest")
    candidate = project / "queue.py"
    candidate.write_text(candidate.read_text() + "\n# interrupted edit\n")

    requested = _configuration().model_copy(update={"objectives": ("total_ops_per_sec:max",)})
    with _create_context(
        project,
        evaluator=evaluator,
        exp_name=run_id,
        existing=True,
        configuration=requested,
    ):
        pass

    stored = state.load_run(run_id)
    assert stored.configuration.objectives == ("total_ops_per_sec:max",)
    assert "# interrupted edit" in candidate.read_text()
    assert "queue.py" in _git(project, "status", "--porcelain")
    assert "# interrupted edit" not in _git(project, "show", "HEAD:queue.py")


def test_collection_resume_pushes_existing_origin_on_teardown(tmp_path):  # noqa: ANN001, ANN201
    source = tmp_path / "input"
    evaluator = _write_project(source)
    runs_dir = tmp_path / "runs"
    with _create_context(source, runs_dir=runs_dir, evaluator=evaluator) as first:
        project = first.project_root
        run_id = first.run_id

    remote = tmp_path / "remote.git"
    subprocess.run(  # noqa: S603
        ["git", "init", "--bare", "-q", str(remote)],  # noqa: S607
        check=True,
    )
    subprocess.run(  # noqa: S603
        ["git", "remote", "add", "origin", str(remote)],  # noqa: S607
        cwd=project,
        check=True,
    )

    with _create_context(
        project,
        runs_dir=runs_dir,
        evaluator=project / "_evaluator" / "checker",
        exp_name=run_id,
        existing=True,
    ):
        pass

    branch = subprocess.run(  # noqa: S603
        ["git", "--git-dir", str(remote), "branch", "--list", f"vibesys-runs/{run_id}"],  # noqa: S607
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert f"vibesys-runs/{run_id}" in branch


def test_direct_resume_republishes_an_already_published_run(tmp_path):  # noqa: ANN001, ANN201
    project = tmp_path / "queue"
    evaluator = _write_project(project)
    with _create_context(project, evaluator=evaluator) as first:
        run_id = first.run_id

    remote = tmp_path / "remote.git"
    subprocess.run(  # noqa: S603
        ["git", "init", "--bare", "-q", str(remote)],  # noqa: S607
        check=True,
    )
    subprocess.run(  # noqa: S603
        ["git", "remote", "add", "origin", str(remote)],  # noqa: S607
        cwd=project,
        check=True,
    )
    subprocess.run(  # noqa: S603
        ["git", "push", "-q", "-u", "origin", f"vibesys-runs/{run_id}"],  # noqa: S607
        cwd=project,
        check=True,
    )

    with (
        patch("vibesys.context.ExperimentRepository.push") as push,
        _create_context(
            project,
            evaluator=evaluator,
            exp_name=run_id,
            existing=True,
        ),
    ):
        pass

    push.assert_called_once_with()


def test_direct_resume_does_not_publish_an_untracked_source_origin(tmp_path):  # noqa: ANN001, ANN201
    project = tmp_path / "queue"
    evaluator = _write_project(project)
    with _create_context(project, evaluator=evaluator) as first:
        run_id = first.run_id

    remote = tmp_path / "remote.git"
    subprocess.run(  # noqa: S603
        ["git", "init", "--bare", "-q", str(remote)],  # noqa: S607
        check=True,
    )
    _git(project, "remote", "add", "origin", str(remote))

    with (
        patch("vibesys.context.ExperimentRepository.push") as push,
        _create_context(
            project,
            evaluator=evaluator,
            exp_name=run_id,
            existing=True,
        ),
    ):
        pass

    push.assert_not_called()


def test_explicit_repository_rejects_a_different_existing_origin(tmp_path):  # noqa: ANN001, ANN201
    project = tmp_path / "queue"
    evaluator = _write_project(project)
    _git(project, "init", "-q", "-b", "main")
    _git(project, "add", ".")
    _git(
        project,
        "-c",
        "user.name=test",
        "-c",
        "user.email=test@example.com",
        "commit",
        "-q",
        "-m",
        "initial",
    )
    _git(project, "remote", "add", "origin", "https://github.com/example/source.git")

    with pytest.raises(ConfigurationError) as caught:
        _create_context(
            project,
            evaluator=evaluator,
            remote_repo="example/destination",
        )

    assert caught.value.diagnostic.code == "repository_setup_failed"
    assert "does not match" in caught.value.diagnostic.message


def test_direct_run_rejects_unmaterialized_workspace_source(tmp_path):  # noqa: ANN001, ANN201
    project = tmp_path / "queue"
    evaluator = _write_project(project)
    source = WorkspaceSource(
        name="library",
        repo="https://example.invalid/library.git",
        commit="0123456",
        dest="library",
    )

    with pytest.raises(ConfigurationError, match="pass --runs-dir"):
        create_run_context(
            config={"model": {"name": "gpt-test"}},  # pyright: ignore[reportArgumentType]
            exp_name="queue",
            runs_dir=None,
            input_path=str(project),
            accuracy_command="true",
            benchmark_command="true",
            workspace_sources=(source,),
            evaluator_path=evaluator,
            project_configuration=_configuration(),
            profiler_kind=ProfilerKind.NONE,
            profiler_domain=DomainName.GENERIC,
            run_environment=RunEnvironmentSpec("local"),
            agent_backend="stub",
        )


def test_omnigent_accepts_active_profiler_configuration(tmp_path):  # noqa: ANN001, ANN201
    project = tmp_path / "queue"
    evaluator = _write_project(project)
    configuration = _configuration().model_copy(
        update={
            "agent_backend": "cli",
            "agent_driver": "omnigent",
            "cli_provider": "codex",
            "profiler": "macos_cpu",
        }
    )

    with create_run_context(
        config={  # pyright: ignore[reportArgumentType]
            "model": {"name": "gpt-test"},
            "agent": {"backend": "cli", "driver": "omnigent", "cli_provider": "codex"},
        },
        exp_name="queue",
        runs_dir=None,
        input_path=str(project),
        accuracy_command="python _evaluator/checker/check.py",
        benchmark_command="python _evaluator/checker/check.py",
        evaluator_path=evaluator,
        project_configuration=configuration,
        profiler_kind=ProfilerKind.MACOS_CPU,
        profiler_domain=DomainName.GENERIC,
        run_environment=RunEnvironmentSpec("local"),
        agent_state_model_type=AgentRunState,
    ) as context:
        assert context.profiler_kind is ProfilerKind.MACOS_CPU


def test_portable_state_snapshot_replaces_namespace_exactly(tmp_path):  # noqa: ANN001, ANN201
    project = tmp_path / "queue"
    evaluator = _write_project(project)

    with _create_context(project, evaluator=evaluator) as ctx:
        state = ctx.state.portable(RunStateNamespace.EVOLVE)
        state.save("old.json", PlainLoopCursor(round_idx=1))
        ctx.state.commit("state 1", state)

        state.delete("old.json")
        state.save("new.json", PlainLoopCursor(round_idx=2))
        ctx.state.commit("state 2", state)

    tree = _git(project, "ls-tree", "-r", "--name-only", "HEAD")
    portable = ctx.project.state.portable_namespace(ctx.run_id, "evolve")
    assert portable.agent_visible_path("new.json") in tree
    assert portable.agent_visible_path("old.json") not in tree


def test_candidate_context_uses_project_worktree_directory(tmp_path):  # noqa: ANN001, ANN201
    project = tmp_path / "queue"
    evaluator = _write_project(project)

    with _create_context(project, evaluator=evaluator) as parent:
        parent_commit = parent.git.current_sha()
        assert parent_commit is not None
        candidate = create_candidate_context(
            parent,
            config={"model": {"name": "gpt-test"}},  # pyright: ignore[reportArgumentType]
            generation=2,
            child_idx=3,
            parent_commit=parent_commit,
            agent_backend="stub",
        )
        candidate_root = candidate.workspace
        assert candidate_root == parent.project.state.candidate_worktree_directory(
            parent.run_id,
            "g2c3",
        )
        assert candidate.log_dir == (
            parent.state.local(RunStateNamespace.EVOLVE).external_directory("candidates/g2c3/logs")
        )
        assert Path(candidate.objective_location).read_text() == parent.effective_objective
        candidate.close()
        assert not candidate_root.exists()


def test_construction_failure_removes_new_copy_and_tears_down_hooks(tmp_path):  # noqa: ANN001, ANN201
    source = tmp_path / "input"
    evaluator = _write_project(source)
    runs_dir = tmp_path / "runs"
    hooks = _RecordingHooks()

    with (
        patch("vibesys.context.build_agent_client", side_effect=RuntimeError("runner failed")),
        pytest.raises(RuntimeError, match="runner failed"),
    ):
        _create_context(source, runs_dir=runs_dir, evaluator=evaluator, hooks=hooks)

    assert hooks.prepared == 1
    assert hooks.torn_down == 1
    assert not runs_dir.exists() or not list(runs_dir.iterdir())


def test_hook_teardown_runs_when_provisioning_fails(tmp_path):  # noqa: ANN001, ANN201
    source = tmp_path / "input"
    evaluator = _write_project(source)
    hooks = _RecordingHooks()

    with (
        patch("vibesys.context.provision_project", side_effect=RuntimeError("copy failed")),
        pytest.raises(RuntimeError, match="copy failed"),
    ):
        _create_context(source, runs_dir=tmp_path / "runs", evaluator=evaluator, hooks=hooks)

    assert hooks.prepared == 1
    assert hooks.torn_down == 1


def test_log_switch_retargets_stderr_tee(tmp_path):  # noqa: ANN001, ANN201
    ctx = object.__new__(_RunContext)
    original_stderr = sys.stderr
    ctx.logger = RunLogger(tmp_path)
    ctx._paths = RunPaths(  # noqa: SLF001
        project_root=tmp_path,
        log_dir=tmp_path,
        run_log_path=ctx.logger.path,
    )
    original_file = ctx.logger.file
    ctx.agent_client = MagicMock()

    ctx.switch_log_file("round001")

    assert original_file.closed
    ctx.agent_client.set_log_file.assert_called_once_with(ctx.logger.writer)
    print("\033[31mcolored diagnostic\033[0m", file=sys.stderr)  # noqa: T201
    ctx.logger.close()
    assert sys.stderr is original_stderr
    assert "colored diagnostic" in ctx.run_log_path.read_text()
    assert "\033[31m" not in ctx.run_log_path.read_text()
