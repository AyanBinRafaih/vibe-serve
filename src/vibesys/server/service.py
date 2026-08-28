"""Presentation-neutral supervision application service."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from vibesys.loops.agent.hypotheses import reproject_run_evidence
from vibesys.loops.agent.state import AgentRunStateStore
from vibesys.server.chat_options import build_chat_options
from vibesys.server.events import EventType, RunEvent
from vibesys.server.experiments import build_experiment_log
from vibesys.server.inspector import RunInspector
from vibesys.server.protocol import (
    ActiveAgentExecution,
    ChatOptions,
    ChatOptionsQuery,
    ChatQuery,
    ChatResult,
    ChatThreadCreateQuery,
    ChatThreadInfo,
    CommandAck,
    EventsQuery,
    ExperimentQuery,
    HistoryQuery,
    HypothesisEntry,
    PauseCommand,
    PerformanceQuery,
    PerformanceRound,
    ProtocolRequest,
    Response,
    ResumeCommand,
    RunSnapshot,
    SnapshotQuery,
    SteerCommand,
)
from vibesys.server.supervisor import RunSupervisor  # noqa: TC001  # tracked: #288

if TYPE_CHECKING:
    from vibesys.loops.agent.model import AgentRunState


class SupervisionService:
    """Authoritative message API consumed by every presentation client."""

    def __init__(self, supervisor: RunSupervisor):  # noqa: ANN204, D107  # tracked: #288
        self.supervisor = supervisor
        self.inspector = RunInspector(supervisor)

    def execute(self, request: ProtocolRequest) -> Response:  # noqa: D102, PLR0911  # tracked: #288
        if isinstance(request, (PauseCommand, ResumeCommand, SteerCommand)):
            return self._execute_command(request)
        if isinstance(request, ChatQuery):
            return self._execute_chat(request)
        if isinstance(request, ChatThreadCreateQuery):
            return self._execute_chat_thread_create(request)
        if isinstance(request, ChatOptionsQuery):
            return Response(request_id=request.request_id, chat_options=self.chat_options())
        if isinstance(request, HistoryQuery):
            self.supervisor.record(EventType.STATUS_QUERY, "/history")
            return Response(request_id=request.request_id, events=self.history_events())
        if isinstance(request, PerformanceQuery):
            self.supervisor.record(EventType.STATUS_QUERY, "/perf")
            return Response(request_id=request.request_id, performance=self.performance_rounds())
        if isinstance(request, ExperimentQuery):
            self.supervisor.record(EventType.STATUS_QUERY, "/experiments")
            ready = self.supervisor.project_run is not None
            experiments = self.experiments() if ready else []
            return Response(
                request_id=request.request_id,
                experiments=experiments,
                experiments_ready=ready,
            )
        if isinstance(request, SnapshotQuery):
            return Response(request_id=request.request_id, snapshot=self.snapshot())
        if isinstance(request, EventsQuery):
            timeout = request.timeout_ms / 1000 if request.timeout_ms else None
            events = (
                self.wait_for_events(request.after_sequence, timeout, request.before_sequence)
                if timeout is not None
                else self.events(request.after_sequence, request.before_sequence)
            )
            return Response(request_id=request.request_id, events=events)
        raise TypeError(f"Unsupported protocol request: {type(request).__name__}")  # noqa: TRY003  # tracked: #288

    def _execute_command(self, request: PauseCommand | ResumeCommand | SteerCommand) -> Response:
        if isinstance(request, PauseCommand):
            self.supervisor.pause_after_call()
            ack = CommandAck(action="pause", status="pending")
        elif isinstance(request, ResumeCommand):
            self.supervisor.resume()
            ack = CommandAck(action="resume", status="consumed")
        else:
            self.supervisor.steer(request.text)
            ack = CommandAck(action="steer", status="pending")
        return Response(request_id=request.request_id, ack=ack)

    def _execute_chat(self, request: ChatQuery) -> Response:
        sequence = self.supervisor.snapshot().sequence
        answer = self.supervisor.chat(request.text, thread_id=request.thread_id)
        return Response(
            request_id=request.request_id,
            chat=ChatResult(question=request.text, answer=answer, thread_id=request.thread_id),
            events=self.supervisor.read_events(sequence),
        )

    def _execute_chat_thread_create(self, request: ChatThreadCreateQuery) -> Response:
        sequence = self.supervisor.snapshot().sequence
        spec = self.supervisor.create_chat_thread(
            driver=request.driver,
            provider=request.provider,
            model=request.model,
            title=request.title,
        )
        return Response(
            request_id=request.request_id,
            chat_thread=ChatThreadInfo(
                thread_id=spec.thread_id,
                title=spec.title,
                driver=spec.driver,
                provider=spec.provider,
                model=spec.model,
            ),
            events=self.supervisor.read_events(sequence),
        )

    def chat_options(self) -> ChatOptions | None:
        """Enumerate the run's chat agent selections, or None before attach."""
        settings = self.supervisor.chat_run_settings
        return None if settings is None else build_chat_options(settings)

    def snapshot(self) -> RunSnapshot:  # noqa: D102  # tracked: #288
        return self.supervisor.snapshot()

    def events(self, after_sequence: int = 0, before_sequence: int | None = None) -> list[RunEvent]:
        """Read the events in the half-open window after a client's cursor."""
        return self.supervisor.read_events(after_sequence, before_sequence)

    def subscription_checkpoint(
        self, after_sequence: int, *, bootstrap_spine: bool = False
    ) -> tuple[int, list[RunEvent], list[ActiveAgentExecution]]:
        """Return one sequence-consistent replay and activity checkpoint."""
        return self.supervisor.subscription_checkpoint(
            after_sequence, bootstrap_spine=bootstrap_spine
        )

    def history_events(self) -> list[RunEvent]:  # noqa: D102  # tracked: #288
        return self.supervisor.read_history_events()

    def performance_rounds(self) -> list[PerformanceRound]:  # noqa: D102  # tracked: #288
        state = self._agent_run_state()
        if state is None:
            return []
        rounds: list[PerformanceRound] = []
        for record in state.rounds:
            if record.perf_metric is None or record.perf_unit is None:
                continue
            rounds.append(
                PerformanceRound(
                    round=record.round_number,
                    perf_metric=record.perf_metric,
                    perf_unit=record.perf_unit,
                    passed=record.passed,
                    profile_skipped=record.profile_skipped,
                )
            )
        return rounds

    def experiments(self) -> list[HypothesisEntry]:
        """Project the run's authoritative hypothesis aggregate for clients."""
        state = self._agent_run_state()
        return [] if state is None else build_experiment_log(state)

    def _agent_run_state(self) -> AgentRunState | None:
        """Load the authoritative agent state, adapting legacy runs in memory."""
        project_run = self.supervisor.project_run
        if project_run is None:
            return None
        manifest = project_run.project.state.load_run(project_run.run_id)
        if manifest.configuration.outer_loop != "agent":
            return None
        portable = project_run.project.state.portable_namespace(project_run.run_id, "agent")
        store = AgentRunStateStore(portable)
        state = store.load_optional()
        if state is None:
            # Old runs are adapted once at the persistence boundary. The
            # server remains a read-only consumer of AgentRunState.
            from vibesys.run.state import RunStateNamespace  # noqa: PLC0415  # tracked: #288

            local = project_run.project.state.local_namespace(
                project_run.run_id, RunStateNamespace.AGENT
            )
            return store.migrate_legacy(
                rounds=project_run.project.state.load_rounds(project_run.run_id),
                local_namespace=local,
                legacy_directions=_metric_directions(manifest.configuration.objectives),
            )
        return reproject_run_evidence(
            state, legacy_directions=_metric_directions(manifest.configuration.objectives)
        )

    def wait_for_events(
        self,
        after_sequence: int,
        timeout: float | None = None,
        before_sequence: int | None = None,
    ) -> list[RunEvent]:
        """Block for new events after the cursor, bounded by ``before_sequence``."""
        return self.supervisor.wait_for_events(after_sequence, timeout, before_sequence)


def _metric_directions(
    encoded: tuple[str, ...],
) -> dict[str, Literal["max", "min"]]:
    """Decode objective directions stored with an agent run."""
    directions: dict[str, Literal["max", "min"]] = {}
    for value in encoded:
        name, separator, direction = value.rpartition(":")
        if separator and name:
            if direction == "max":
                directions[name] = "max"
            elif direction == "min":
                directions[name] = "min"
    return directions
