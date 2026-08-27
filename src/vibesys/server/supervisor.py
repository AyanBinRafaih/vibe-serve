"""Thread-safe human controls at agent invocation boundaries."""

from __future__ import annotations

import asyncio
import re
import threading
import time
import uuid
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path  # noqa: TC003  # tracked: #288
from typing import TYPE_CHECKING, Any

from vibesys.server.chat_options import ChatRunSettings  # noqa: TC001  # tracked: #288
from vibesys.server.diagnostics import (
    Diagnostic,
    DiagnosticRetryability,
    DiagnosticScope,
    DiagnosticSeverity,
    exception_detail,
    exception_to_diagnostic,
)
from vibesys.server.events import (
    AgentExecutionActivityData,
    AgentExecutionFinishedData,
    AgentExecutionStartedData,
    AgentOutputChannel,
    AgentOutputChunkData,
    ChatData,
    ChatThreadCreatedData,
    EventData,
    EventStatus,
    EventStore,
    EventType,
    InvocationFinishedData,
    InvocationStartedData,
    OutputData,
    OutputStream,
    PhaseData,
    RunEvent,
    TodoUpdateData,
    ToolCallData,
    ToolResultData,
    json_value,
    make_event,
)
from vibesys.server.protocol import ActiveAgentExecution, RunSnapshot

_MAX_EXCEPTION_CHAIN = 8
_TERMINAL_CHAT_DRAIN_TIMEOUT_SECONDS = 5.0
_DIAGNOSTIC_FAILURE_EVENTS = frozenset(
    {
        EventType.CONFIGURATION_FAILED,
        EventType.INVOCATION_FINISHED,
        EventType.AGENT_EXECUTION_FINISHED,
        EventType.PHASE_FINISHED,
        EventType.RUN_FAILED,
        EventType.RUN_INTERRUPTED,
    }
)
_NONTERMINAL_FAILURE_EVENTS = frozenset(
    {
        EventType.INVOCATION_FINISHED,
        EventType.AGENT_EXECUTION_FINISHED,
        EventType.PHASE_FINISHED,
    }
)

if TYPE_CHECKING:
    from vs_project import Project, StateSnapshot


@dataclass(frozen=True)
class ProjectRunState:
    """Typed access to one run's canonical project state."""

    project: Project
    run_id: str

    def history_snapshots(self) -> tuple[StateSnapshot, ...]:
        """Return immutable snapshots used by read-only run inspection."""
        return tuple(
            self.project.state.portable_namespace(self.run_id, namespace).snapshot()
            for namespace in ("agent", "plain", "evolve")
        )


@dataclass(frozen=True)
class AgentExecutionHandle:
    """Identity and effective prompt returned by an execution start boundary."""

    execution_id: str
    user_prompt: str


@dataclass(frozen=True)
class TerminalChatResource:
    """Chat handler and resources retained while the terminal UI is open."""

    handler: Callable[[str], str]
    close: Callable[[], None]


@dataclass(frozen=True)
class ChatThreadHandle:
    """Resolved thread settings and the handler that answers its questions."""

    spec: ChatThreadCreatedData
    handler: Callable[[str], str]


ChatThreadFactory = Callable[[str, str | None, str | None, str | None], ChatThreadHandle]
"""Builds one thread's chat service: (thread_id, driver, provider, model).

None arguments resolve to the run's configured defaults. The factory raises
``ValueError`` for an unsupported driver/provider combination; resource
cleanup for the built service stays with the factory's owner (the run
context), not the supervisor."""


class RunSupervisor:
    """Own pause state, invocation metadata, and the run audit store."""

    def __init__(self) -> None:  # noqa: D107  # tracked: #288
        self._condition = threading.Condition()
        self._pause_after_call = False
        self._paused = False
        self._pending_steer: list[str] = []
        self._active_executions: dict[str, ActiveAgentExecution] = {}
        self._execution_todo_summaries: dict[str, str] = {}
        self._execution_active_tools: dict[str, list[str]] = {}
        self._run_control_execution_ids: set[str] = set()
        self._canonical_execution_ids: set[str] = set()
        self._legacy_invocation_ids: set[str] = set()
        self._run_status = "starting"
        # One durable event stream is both the live subscription source and
        # the replay source. A process starts on a bootstrap directory before
        # it knows the project run; ``attach`` moves that short prefix into the
        # run's durable store once the context is available.
        self._store: EventStore | None = None
        self._pending_events: list[RunEvent] = []
        self.log_dir: Path | None = None
        self._project_run: ProjectRunState | None = None
        self._current_kind: str | None = None
        self._current_round: str | None = None
        self._chat_handler: Callable[[str], str] | None = None
        # Per-thread chat routing. Specs replay from CHAT_THREAD_CREATED
        # events so a resumed run can rebuild handlers on demand through the
        # context-owned factory.
        self._chat_thread_factory: ChatThreadFactory | None = None
        self._chat_thread_handlers: dict[str, Callable[[str], str]] = {}
        self._chat_thread_specs: dict[str, ChatThreadCreatedData] = {}
        # The run's own agent selection, attached by the run context. Chat
        # options are derived from it, so a client enumerates nothing.
        self._chat_run_settings: ChatRunSettings | None = None
        self._active_chat_calls = 0
        self._retain_terminal_chat = False
        self._terminal_chat_resource: TerminalChatResource | None = None
        self._retired_terminal_chat_resource: TerminalChatResource | None = None
        self._presentation_local = threading.local()
        self._legacy_execution_local = threading.local()
        # An invocation and its terminal run failure often carry the same
        # exception. Keep one diagnostic object so both events identify the
        # same operator-visible failure without reformatting it at each layer.
        self._error_diagnostics: dict[int, tuple[BaseException, Diagnostic]] = {}

    @property
    def current_round(self) -> str | None:  # noqa: D102  # tracked: #288
        with self._condition:
            return self._current_round

    @property
    def project_run(self) -> ProjectRunState | None:
        """Return canonical project state when a run context has attached."""
        with self._condition:
            return self._project_run

    def attach(
        self,
        log_dir: Path,
        *,
        project: Project | None = None,
        run_id: str | None = None,
    ) -> None:
        """Attach event logging, optionally with canonical project-run state.

        The headless server first attaches a bootstrap event directory before
        CLI parsing creates a project. The run context later supplies both the
        project and run ID, which readers use for persisted run metadata.
        """
        if (project is None) != (run_id is None):
            raise ValueError("project and run_id must be provided together")  # noqa: TRY003  # tracked: #288
        log_dir.mkdir(parents=True, exist_ok=True)
        events_path = log_dir / "run-events.jsonl"
        with self._condition:
            if project is not None and run_id is not None:
                self._project_run = ProjectRunState(project, run_id)
            store = self._store
            if store is not None and store.path == events_path:
                if run_id is not None:
                    store.run_id = run_id
                self.log_dir = log_dir
                return
            durable = EventStore(events_path, run_id=run_id or log_dir.parent.name)
            # A resumed run already owns durable events. Index them before
            # replay so legacy invocation records retain their canonical
            # execution projection.
            self._index_execution_lifecycle(durable.read())
            pending = store.read() if store is not None else self._pending_events
            self._pending_events = []
            # Appending through EventStore gives bootstrap events the next
            # durable sequence numbers, rather than preserving a second,
            # colliding sequence space from the temporary server directory.
            for event in pending:
                self._index_execution_lifecycle([durable.append(event)])
            self._store = durable
            self.log_dir = log_dir
            started_fresh = store is None
        if started_fresh:
            self.record(EventType.SERVER_STARTED, status=EventStatus.ACTIVE)
        with self._condition:
            self._run_status = "running"

    def publish_output(self, stream: OutputStream, content: str, source: str = "backend") -> None:  # noqa: D102  # tracked: #288
        if not content:
            return
        self.record(
            EventType.OUTPUT,
            data=OutputData(stream=stream, source=source, content=content),
        )

    def publish_agent_output(  # noqa: D102  # tracked: #288
        self,
        content: str,
        *,
        channel: AgentOutputChannel = "assistant",
        agent_kind: str | None = None,
        round_label: str | None = None,
        invocation_id: str | None = None,
    ) -> None:
        if not content:
            return
        self.publish_presentation(
            EventType.AGENT_OUTPUT_CHUNK,
            AgentOutputChunkData(channel=channel, content=content),
            agent_kind=agent_kind,
            round_label=round_label,
            invocation_id=invocation_id,
        )

    def publish_presentation(
        self,
        event_type: EventType,
        data: EventData,
        *,
        agent_kind: str | None = None,
        round_label: str | None = None,
        invocation_id: str | None = None,
    ) -> None:
        """Record a presentation event enriched with the active invocation scope."""
        scoped_kind = getattr(self._presentation_local, "agent_kind", None)
        scoped_round = getattr(self._presentation_local, "round_label", None)
        scoped_invocation = getattr(self._presentation_local, "invocation_id", None)
        execution_id = invocation_id or scoped_invocation
        if execution_id is not None:
            activity = self._activity_for_presentation(event_type, data, execution_id)
            if activity is not None:
                self.update_agent_execution_activity(execution_id, activity)
        self.record(
            event_type,
            agent_kind=agent_kind or scoped_kind or self._current_kind,
            round_label=round_label or scoped_round or self._current_round,
            execution_id=execution_id,
            data=data,
        )

    def _activity_for_presentation(
        self, event_type: EventType, data: EventData, execution_id: str
    ) -> AgentExecutionActivityData | None:
        if event_type is EventType.AGENT_OUTPUT_CHUNK and isinstance(data, AgentOutputChunkData):
            with self._condition:
                if self._execution_active_tools.get(execution_id):
                    return None
            return _text_activity(data)
        if event_type is EventType.TOOL_CALL and isinstance(data, ToolCallData):
            return self._tool_call_activity(execution_id, data)
        if event_type is EventType.TODO_UPDATE and isinstance(data, TodoUpdateData):
            return self._todo_activity(execution_id, data)
        if event_type is EventType.TOOL_RESULT and isinstance(data, ToolResultData):
            return self._tool_result_activity(execution_id, data)
        return None

    def _tool_call_activity(
        self, execution_id: str, data: ToolCallData
    ) -> AgentExecutionActivityData:
        with self._condition:
            self._execution_active_tools.setdefault(execution_id, []).append(data.tool)
        return AgentExecutionActivityData(mode="tool", summary=f"Using {data.tool}", tool=data.tool)

    def _todo_activity(
        self, execution_id: str, data: TodoUpdateData
    ) -> AgentExecutionActivityData | None:
        current = next((todo.content for todo in data.todos if todo.status == "in_progress"), None)
        with self._condition:
            if current is None:
                self._execution_todo_summaries.pop(execution_id, None)
                if self._execution_active_tools.get(execution_id):
                    return None
                return AgentExecutionActivityData(mode="thinking", summary="Thinking")
            self._execution_todo_summaries[execution_id] = current
            if self._execution_active_tools.get(execution_id):
                return None
        return AgentExecutionActivityData(mode="thinking", summary=current)

    def _tool_result_activity(
        self, execution_id: str, data: ToolResultData
    ) -> AgentExecutionActivityData | None:
        with self._condition:
            if execution_id not in self._active_executions:
                return None
            tools = self._execution_active_tools.get(execution_id, [])
            if data.tool in tools:
                tools.remove(data.tool)
            remaining_tool = tools[-1] if tools else None
            todo_summary = self._execution_todo_summaries.get(execution_id)
        if remaining_tool is not None:
            return AgentExecutionActivityData(
                mode="tool", summary=f"Using {remaining_tool}", tool=remaining_tool
            )
        return AgentExecutionActivityData(mode="thinking", summary=todo_summary or "Thinking")

    def update_agent_execution_activity(
        self, execution_id: str, activity: AgentExecutionActivityData
    ) -> None:
        """Replace one active execution's semantic activity and publish it once."""
        with self._condition:
            active = self._active_executions.get(execution_id)
            if active is None or active.activity == activity:
                return
            self.record(
                EventType.AGENT_EXECUTION_ACTIVITY_CHANGED,
                status=EventStatus.ACTIVE,
                agent_kind=active.agent_kind,
                round_label=active.round_label,
                execution_id=execution_id,
                data=activity,
            )
            self._active_executions[execution_id] = active.model_copy(update={"activity": activity})

    def record(  # noqa: D102  # tracked: #288
        self,
        event_type: EventType,
        text: str = "",
        *,
        data: EventData | None = None,
        **fields: Any,  # noqa: ANN401  # tracked: #288
    ) -> RunEvent:
        if (
            event_type in _DIAGNOSTIC_FAILURE_EVENTS
            and fields.get("status") in {EventStatus.FAILED, EventStatus.FAILED.value}
            and fields.get("diagnostic") is None
        ):
            raise ValueError(f"Failed {event_type.value} events must include a diagnostic")  # noqa: TRY003  # contract violation, not a user-facing error
        event = make_event(event_type, text, data=data, **fields)
        with self._condition:
            store = self._store
            if store is None:
                self._pending_events.append(event)
                return event
            recorded = store.append(event)
            self._index_execution_lifecycle([recorded])
            return recorded

    def record_failure(  # noqa: PLR0913  # failure event fields belong at this boundary
        self,
        event_type: EventType,
        error: BaseException,
        *,
        scope: DiagnosticScope,
        operation: str,
        data: EventData | Callable[[Diagnostic], EventData] | None = None,
        text: str | None = None,
        severity: DiagnosticSeverity = DiagnosticSeverity.ERROR,
        status: EventStatus = EventStatus.FAILED,
        diagnostic: Diagnostic | None = None,
        **fields: Any,  # noqa: ANN401  # tracked: #288
    ) -> RunEvent:
        """Record a failed operational event with its canonical diagnostic.

        This API handles nonterminal invocation and phase boundaries. Pass an
        existing diagnostic when related events must share an identity.
        Terminal failures remain owned by ``finish`` and ``run_server``.
        """
        if event_type not in _NONTERMINAL_FAILURE_EVENTS:
            raise ValueError(f"Cannot record {event_type.value} without owning run termination")  # noqa: TRY003  # contract violation, not a user-facing error
        return self._record_failure_event(
            event_type,
            error,
            scope=scope,
            operation=operation,
            data=data,
            text=text,
            severity=severity,
            status=status,
            diagnostic=diagnostic,
            **fields,
        )

    def _record_failure_event(  # noqa: PLR0913  # failure event fields belong at this boundary
        self,
        event_type: EventType,
        error: BaseException,
        *,
        scope: DiagnosticScope,
        operation: str,
        data: EventData | Callable[[Diagnostic], EventData] | None = None,
        text: str | None = None,
        severity: DiagnosticSeverity = DiagnosticSeverity.ERROR,
        status: EventStatus = EventStatus.FAILED,
        diagnostic: Diagnostic | None = None,
        **fields: Any,  # noqa: ANN401  # tracked: #288
    ) -> RunEvent:
        """Record an operational failure after its lifecycle owner is known."""
        if event_type not in _DIAGNOSTIC_FAILURE_EVENTS:
            raise ValueError(f"{event_type.value} is not an operational failure event")  # noqa: TRY003  # contract violation, not a user-facing error
        diagnostic = diagnostic or self._diagnostic_for(error, scope, operation=operation)
        if diagnostic.severity is not severity:
            diagnostic = diagnostic.model_copy(update={"severity": severity})
        event_data = data(diagnostic) if callable(data) else data
        return self.record(
            event_type,
            diagnostic.summary if text is None else text,
            status=status,
            data=event_data,
            diagnostic=diagnostic,
            **fields,
        )

    @contextmanager
    def capture_failure(  # noqa: PLR0913  # failure event fields belong at this boundary
        self,
        *,
        event_type: EventType,
        scope: DiagnosticScope,
        operation: str,
        data: EventData | Callable[[Diagnostic], EventData] | None = None,
        text: str | None = None,
        severity: DiagnosticSeverity = DiagnosticSeverity.ERROR,
        **fields: Any,  # noqa: ANN401  # tracked: #288
    ) -> Generator[None]:
        """Capture one independent background operation's failure.

        Use only for new entry points without an existing failure boundary.
        Do not wrap LoopContext agent calls or ``run_server``: ``after_agent``
        and ``finish`` already record those failures. This helper records one
        failure and re-raises it, but never finishes the run.
        """
        if event_type not in _NONTERMINAL_FAILURE_EVENTS:
            raise ValueError(f"Cannot capture {event_type.value} without owning run termination")  # noqa: TRY003  # contract violation, not a user-facing error
        try:
            yield
        except BaseException as error:
            self.record_failure(
                event_type,
                error,
                scope=scope,
                operation=operation,
                data=data,
                text=text,
                severity=severity,
                **fields,
            )
            raise

    def read_events(self, after_sequence: int = 0) -> list[RunEvent]:  # noqa: D102  # tracked: #288
        with self._condition:
            store = self._store
            if store is None:
                return []
            return _canonical_execution_events(
                store.read(after_sequence),
                canonical_lifecycle_ids=self._canonical_execution_ids,
                invocation_lifecycle_ids=self._legacy_invocation_ids,
            )

    def read_history_events(self) -> list[RunEvent]:
        """Return the same durable event history used for subscription replay."""
        store = self._store
        return _canonical_execution_events(store.read()) if store else []

    def wait_for_events(self, after_sequence: int, timeout: float | None = None) -> list[RunEvent]:  # noqa: D102  # tracked: #288
        store = self._store
        if store is None:
            return []
        store.wait(after_sequence, timeout)
        with self._condition:
            return _canonical_execution_events(
                store.read(after_sequence),
                canonical_lifecycle_ids=self._canonical_execution_ids,
                invocation_lifecycle_ids=self._legacy_invocation_ids,
            )

    def snapshot(self) -> RunSnapshot:  # noqa: D102  # tracked: #288
        with self._condition:
            store = self._store
            return RunSnapshot(
                run_id=store.run_id if store else "",
                sequence=store.last_sequence if store else 0,
                status="paused" if self._paused else self._run_status,
                agent_kind=self._current_kind,
                round_label=self._current_round,
                active_executions=[
                    execution.model_copy(deep=True)
                    for execution in self._active_executions.values()
                ],
            )

    def subscription_checkpoint(
        self, after_sequence: int
    ) -> tuple[int, list[RunEvent], list[ActiveAgentExecution]]:
        """Atomically capture replay events and active state at one watermark."""
        with self._condition:
            store = self._store
            through_sequence = store.last_sequence if store else 0
            events = store.read(after_sequence) if store else []
            events = _canonical_execution_events(
                events,
                canonical_lifecycle_ids=self._canonical_execution_ids,
                invocation_lifecycle_ids=self._legacy_invocation_ids,
            )
            events = [event for event in events if event.sequence <= through_sequence]
            active = [
                execution.model_copy(deep=True) for execution in self._active_executions.values()
            ]
            return through_sequence, events, active

    def _index_execution_lifecycle(self, events: list[RunEvent]) -> None:
        for event in events:
            if event.type is EventType.CHAT_THREAD_CREATED and isinstance(
                event.data, ChatThreadCreatedData
            ):
                self._chat_thread_specs.setdefault(event.data.thread_id, event.data)
            if (
                event.type is EventType.CHAT
                and event.chat_thread_id is not None
                and isinstance(event.data, ChatData)
                and event.data.thread_title
            ):
                spec = self._chat_thread_specs.get(event.chat_thread_id)
                if spec is not None and not spec.title:
                    self._chat_thread_specs[event.chat_thread_id] = spec.model_copy(
                        update={"title": event.data.thread_title}
                    )
            if event.execution_id is None:
                continue
            if event.type in {
                EventType.AGENT_EXECUTION_STARTED,
                EventType.AGENT_EXECUTION_FINISHED,
            }:
                self._canonical_execution_ids.add(event.execution_id)
            elif event.type in {EventType.INVOCATION_STARTED, EventType.INVOCATION_FINISHED}:
                self._legacy_invocation_ids.add(event.execution_id)

    def chat_agent_available(self) -> bool:
        """True when an agent-backed chat handler is installed for this run.

        A server-backed run may replace its live handler with a separately
        owned terminal handler after normal run teardown.
        """
        with self._condition:
            return self._chat_handler is not None

    def chat(self, text: str, thread_id: str | None = None) -> str:  # noqa: D102  # tracked: #288
        if thread_id is not None:
            return self._thread_chat(text, thread_id)
        with self._condition:
            handler = self._chat_handler
            if handler is not None:
                self._active_chat_calls += 1
        try:
            if handler is None:
                from vibesys.server.inspector import RunInspector  # noqa: PLC0415  # tracked: #288

                # No agent is reachable, so say that rather than answering as if
                # this were the normal path. The keyword diagnostic is still worth
                # showing, but it is supporting detail, not the answer.
                answer = (
                    "The experiment chat agent is not available for this run"
                    f" ({self._chat_unavailable_reason()}), so this is a read-only"
                    " summary from the recorded events rather than an answer.\n\n"
                    + RunInspector(self).answer(text)
                )
            else:
                answer = handler(text)
            self.record(
                EventType.CHAT,
                text,
                status=EventStatus.ANSWERED,
                agent_kind="chat",
                round_label="experiment-chat",
                data=ChatData(answer=answer),
            )
            return answer
        finally:
            if handler is not None:
                self._release_chat_call()

    def _thread_chat(self, text: str, thread_id: str) -> str:
        """Route one question to a created thread's handler and audit it."""
        handler = self._resolve_thread_handler(thread_id)
        if isinstance(handler, str):
            # A routing failure is an answer to this caller, not run history:
            # no CHAT event is recorded for a thread that cannot answer.
            return handler
        with self._condition:
            self._active_chat_calls += 1
        try:
            answer = handler(text)
            thread_title = self._title_thread_if_needed(thread_id, text)
            self.record(
                EventType.CHAT,
                text,
                status=EventStatus.ANSWERED,
                agent_kind="chat",
                round_label="experiment-chat",
                chat_thread_id=thread_id,
                data=ChatData(answer=answer, thread_title=thread_title),
            )
            return answer
        finally:
            self._release_chat_call()

    def _title_thread_if_needed(self, thread_id: str, question: str) -> str | None:
        """Derive and store an untitled thread's title from its first message."""
        with self._condition:
            spec = self._chat_thread_specs.get(thread_id)
            if spec is None or spec.title:
                return None
            title = _chat_thread_title(question)
            if not title:
                return None
            self._chat_thread_specs[thread_id] = spec.model_copy(update={"title": title})
            return title

    def _resolve_thread_handler(self, thread_id: str) -> Callable[[str], str] | str:
        """Return the thread's handler, or the error answer explaining why not."""
        with self._condition:
            handler = self._chat_thread_handlers.get(thread_id)
            spec = self._chat_thread_specs.get(thread_id)
            factory = self._chat_thread_factory
        if handler is not None:
            return handler
        if spec is None:
            return (
                f"Unknown experiment chat thread {thread_id!r}. Create one with "
                "/new-chat, or omit the thread to use the default experiment chat."
            )
        if factory is None:
            return (
                f"Experiment chat thread {thread_id!r} cannot answer right now "
                f"({self._chat_unavailable_reason()})."
            )
        try:
            handle = factory(thread_id, spec.driver, spec.provider, spec.model)
        except Exception as exc:  # noqa: BLE001  # routing failures become answers
            return (
                f"Could not restore experiment chat thread {thread_id!r}: "
                f"{type(exc).__name__}: {exc}"
            )
        with self._condition:
            return self._chat_thread_handlers.setdefault(thread_id, handle.handler)

    def set_chat_thread_factory(self, factory: ChatThreadFactory | None) -> None:
        """Install the context-owned builder for per-thread chat services."""
        with self._condition:
            self._chat_thread_factory = factory

    def set_chat_run_settings(self, settings: ChatRunSettings | None) -> None:
        """Record the run's agent selection, the basis of every chat option."""
        with self._condition:
            self._chat_run_settings = settings

    @property
    def chat_run_settings(self) -> ChatRunSettings | None:
        """The run's agent selection, or None before a run context attaches."""
        with self._condition:
            return self._chat_run_settings

    def create_chat_thread(
        self,
        *,
        driver: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        title: str | None = None,
    ) -> ChatThreadCreatedData:
        """Create one chat thread, record its durable event, and register it.

        An untitled thread is titled by the server from its first message.
        """
        with self._condition:
            factory = self._chat_thread_factory
        if factory is None:
            raise RuntimeError(  # noqa: TRY003  # surfaced to the requesting client
                "Experiment chat threads are not available for this run "
                f"({self._chat_unavailable_reason()})"
            )
        thread_id = uuid.uuid4().hex
        handle = factory(thread_id, driver, provider, model)
        spec = handle.spec
        if title is not None and title.strip():
            spec = spec.model_copy(update={"title": title.strip()})
        with self._condition:
            self._chat_thread_specs[spec.thread_id] = spec
            self._chat_thread_handlers[spec.thread_id] = handle.handler
        self.record(
            EventType.CHAT_THREAD_CREATED,
            agent_kind="chat",
            round_label="experiment-chat",
            chat_thread_id=spec.thread_id,
            data=spec,
        )
        return spec

    def chat_threads(self) -> list[ChatThreadCreatedData]:
        """Return every created thread's replayable spec, oldest first."""
        with self._condition:
            return sorted(self._chat_thread_specs.values(), key=lambda spec: spec.created_at)

    def clear_chat_threads_and_drain(self) -> None:
        """Stop routing thread chat and wait for in-flight calls to release."""
        with self._condition:
            self._chat_thread_factory = None
            self._chat_thread_handlers.clear()
            self._wait_for_chat_drain_locked(timeout=_TERMINAL_CHAT_DRAIN_TIMEOUT_SECONDS)

    def _release_chat_call(self) -> None:
        """Release one handler lease and close a retired resource when safe."""
        retired: TerminalChatResource | None = None
        with self._condition:
            self._active_chat_calls -= 1
            if self._active_chat_calls == 0:
                retired = self._retired_terminal_chat_resource
                self._retired_terminal_chat_resource = None
            self._condition.notify_all()
        if retired is None:
            return
        try:
            retired.close()
        except Exception as exc:  # noqa: BLE001  # deferred cleanup cannot reach its caller
            self.publish_output(
                "stderr",
                f"Terminal experiment chat cleanup failed: {type(exc).__name__}: {exc}\n",
                source="terminal-chat",
            )

    def _chat_unavailable_reason(self) -> str:
        with self._condition:
            status = self._run_status
        if status in {"completed", "failed"}:
            return "the run has finished"
        return "the run has not finished starting up"

    def set_chat_handler(self, handler: Callable[[str], str] | None) -> None:
        """Install the current experiment's agent-backed chat handler."""
        with self._condition:
            self._chat_handler = handler

    def clear_chat_handler_and_drain(self) -> None:
        """Stop accepting chat and wait until callers release the live handler."""
        with self._condition:
            self._chat_handler = None
            self._wait_for_chat_drain_locked(timeout=None)

    def enable_terminal_chat_retention(self) -> None:
        """Keep a run context's chat resources until its presentation exits."""
        with self._condition:
            self._retain_terminal_chat = True

    def terminal_chat_retention_enabled(self) -> bool:
        """Return whether the presentation runtime accepts a terminal chat resource."""
        with self._condition:
            return self._retain_terminal_chat

    def retain_terminal_chat_resource(self, resource: TerminalChatResource) -> bool:
        """Take ownership of terminal chat cleanup when retention is enabled."""
        with self._condition:
            if not self._retain_terminal_chat:
                return False
            if self._terminal_chat_resource is not None:
                raise RuntimeError(  # noqa: TRY003
                    "Terminal chat resources are already retained"
                )
            self._terminal_chat_resource = resource
            self._chat_handler = resource.handler
            return True

    def close_terminal_chat_resource(self) -> None:
        """Retire the terminal resource, closing it after its last borrower."""
        with self._condition:
            resource = self._terminal_chat_resource
            self._terminal_chat_resource = None
            self._retain_terminal_chat = False
            if resource is not None and self._chat_handler == resource.handler:
                self._chat_handler = None
            if resource is not None and not self._wait_for_chat_drain_locked(
                timeout=_TERMINAL_CHAT_DRAIN_TIMEOUT_SECONDS
            ):
                self._retired_terminal_chat_resource = resource
                resource = None
        if resource is not None:
            resource.close()

    def _wait_for_chat_drain_locked(self, *, timeout: float | None) -> bool:
        deadline = time.monotonic() + timeout if timeout is not None else None
        while self._active_chat_calls > 0:
            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0:
                return False
            self._condition.wait(timeout=remaining)
        return True

    @contextmanager
    def presentation_scope(
        self, *, agent_kind: str, round_label: str, invocation_id: str
    ) -> Generator[None]:
        """Tag side-channel presentation events without changing active run state."""
        previous = (
            getattr(self._presentation_local, "agent_kind", None),
            getattr(self._presentation_local, "round_label", None),
            getattr(self._presentation_local, "invocation_id", None),
        )
        self._presentation_local.agent_kind = agent_kind
        self._presentation_local.round_label = round_label
        self._presentation_local.invocation_id = invocation_id
        try:
            yield
        finally:
            (
                self._presentation_local.agent_kind,
                self._presentation_local.round_label,
                self._presentation_local.invocation_id,
            ) = previous

    def pause_after_call(self) -> None:  # noqa: D102  # tracked: #288
        with self._condition:
            self._pause_after_call = True
        self.record(EventType.CONTROL, "/pause", status=EventStatus.PENDING)

    def resume(self) -> None:  # noqa: D102  # tracked: #288
        with self._condition:
            self._paused = False
            self._pause_after_call = False
            self._condition.notify_all()
        self.record(EventType.CONTROL, "/resume", status=EventStatus.CONSUMED)

    def steer(self, text: str) -> None:
        """Queue an operator instruction for the next agent invocation.

        The message is drained and appended to the next agent's user prompt in
        :meth:`before_agent`. It applies whether the run is live or paused (in
        which case it takes effect when the run resumes).
        """
        with self._condition:
            self._pending_steer.append(text)
        self.record(EventType.CONTROL, f"/steer: {text}", status=EventStatus.PENDING)

    def start_agent_execution(  # noqa: PLR0913  # lifecycle and control semantics meet here
        self,
        kind: str,
        round_label: str,
        user_prompt: str,
        system_prompt: str = "",
        *,
        consume_steering: bool = True,
        participates_in_run_control: bool = True,
        driver: str | None = None,
        provider: str | None = None,
        model: str | None = None,
    ) -> AgentExecutionHandle:
        """Start one prompt-to-result execution and return its explicit identity."""
        with self._condition:
            while participates_in_run_control and self._paused:
                self._condition.wait()
            steer_messages = (
                self._pending_steer if consume_steering and participates_in_run_control else []
            )
            if consume_steering and participates_in_run_control:
                self._pending_steer = []
            if participates_in_run_control:
                self._current_kind, self._current_round = kind, round_label
            execution_id = uuid.uuid4().hex
            effective_prompt = _with_steering(user_prompt, steer_messages)
            attempt = _attempt_from_label(round_label)
            activity = AgentExecutionActivityData(
                mode="thinking", summary=_initial_activity_summary(kind)
            )
            active = ActiveAgentExecution(
                execution_id=execution_id,
                agent_kind=kind,
                round_label=round_label,
                stage=kind,
                attempt=attempt,
                assignment=effective_prompt,
                started_at=datetime.now(UTC),
                activity=activity,
                driver=driver,
                provider=provider,
                model=model,
            )
            self.record(
                EventType.AGENT_EXECUTION_STARTED,
                status=EventStatus.ACTIVE,
                agent_kind=kind,
                round_label=round_label,
                execution_id=execution_id,
                data=AgentExecutionStartedData(
                    stage=kind,
                    attempt=attempt,
                    system_prompt=system_prompt,
                    user_prompt=effective_prompt,
                    activity=activity,
                    driver=driver,
                    provider=provider,
                    model=model,
                ),
            )
            self._active_executions[execution_id] = active
            if participates_in_run_control:
                self._run_control_execution_ids.add(execution_id)
            legacy_phase = PhaseData(phase=kind, attempt=attempt)
            self.record(
                EventType.PHASE_STARTED,
                status=EventStatus.ACTIVE,
                agent_kind=kind,
                round_label=round_label,
                execution_id=execution_id,
                data=legacy_phase,
            )
            self.record(
                EventType.INVOCATION_STARTED,
                status=EventStatus.ACTIVE,
                agent_kind=kind,
                round_label=round_label,
                execution_id=execution_id,
                data=InvocationStartedData(
                    system_prompt=system_prompt, user_prompt=effective_prompt
                ),
            )
            if steer_messages:
                self.record(
                    EventType.CONTROL,
                    "/steer",
                    status=EventStatus.CONSUMED,
                    agent_kind=kind,
                    round_label=round_label,
                    execution_id=execution_id,
                )
        return AgentExecutionHandle(execution_id=execution_id, user_prompt=effective_prompt)

    def before_agent(
        self, kind: str, round_label: str, user_prompt: str, system_prompt: str = ""
    ) -> str:
        """Compatibility wrapper for callers not yet carrying execution identity."""
        execution = self.start_agent_execution(kind, round_label, user_prompt, system_prompt)
        self._legacy_execution_local.execution_id = execution.execution_id
        return execution.user_prompt

    def after_agent(  # noqa: D102  # tracked: #288
        self,
        kind: str,
        round_label: str,
        *,
        result: Any = None,  # noqa: ANN401  # tracked: #288
        error: BaseException | None = None,  # noqa: ANN401, RUF100  # tracked: #288
        execution_id: str | None = None,
    ) -> None:
        del kind, round_label
        execution_id = execution_id or getattr(self._legacy_execution_local, "execution_id", None)
        if execution_id is None:
            # Compatibility for an old boundary-only caller that reports a
            # safe point without first opening an invocation.
            with self._condition:
                if self._pause_after_call:
                    self._pause_after_call = False
                    self._paused = True
            return
        with self._condition:
            active = self._active_executions.get(execution_id)
            if active is None:
                return
            participates_in_run_control = execution_id in self._run_control_execution_ids
            should_pause = participates_in_run_control and self._pause_after_call
            terminal_status = (
                _execution_error_status(error) if error is not None else EventStatus.COMPLETED
            )
            if error is not None:
                execution_event = self.record_failure(
                    EventType.AGENT_EXECUTION_FINISHED,
                    error,
                    scope=DiagnosticScope.INVOCATION,
                    operation="Agent execution",
                    status=terminal_status,
                    data=lambda diagnostic: AgentExecutionFinishedData(
                        result=json_value(result), error=diagnostic.summary
                    ),
                    agent_kind=active.agent_kind,
                    round_label=active.round_label,
                    execution_id=execution_id,
                )
                diagnostic = execution_event.diagnostic
            else:
                self.record(
                    EventType.AGENT_EXECUTION_FINISHED,
                    status=EventStatus.COMPLETED,
                    data=AgentExecutionFinishedData(result=json_value(result)),
                    agent_kind=active.agent_kind,
                    round_label=active.round_label,
                    execution_id=execution_id,
                )
                diagnostic = None
            self._active_executions.pop(execution_id, None)
            self._run_control_execution_ids.discard(execution_id)
            self._execution_todo_summaries.pop(execution_id, None)
            self._execution_active_tools.pop(execution_id, None)
            if should_pause:
                self._pause_after_call = False
                self._paused = True
            legacy_finished = InvocationFinishedData(
                result=json_value(result), error=diagnostic.summary if diagnostic else None
            )
            if error is not None:
                self.record_failure(
                    EventType.INVOCATION_FINISHED,
                    error,
                    scope=DiagnosticScope.INVOCATION,
                    operation="Agent execution",
                    status=terminal_status,
                    data=legacy_finished,
                    diagnostic=diagnostic,
                    agent_kind=active.agent_kind,
                    round_label=active.round_label,
                    execution_id=execution_id,
                )
                self.record_failure(
                    EventType.PHASE_FINISHED,
                    error,
                    scope=DiagnosticScope.INVOCATION,
                    operation="Agent execution",
                    status=terminal_status,
                    data=PhaseData(phase=active.stage, attempt=active.attempt),
                    diagnostic=diagnostic,
                    agent_kind=active.agent_kind,
                    round_label=active.round_label,
                    execution_id=execution_id,
                )
            else:
                self.record(
                    EventType.INVOCATION_FINISHED,
                    status=EventStatus.COMPLETED,
                    agent_kind=active.agent_kind,
                    round_label=active.round_label,
                    execution_id=execution_id,
                    data=legacy_finished,
                )
                self.record(
                    EventType.PHASE_FINISHED,
                    status=EventStatus.COMPLETED,
                    agent_kind=active.agent_kind,
                    round_label=active.round_label,
                    execution_id=execution_id,
                    data=PhaseData(phase=active.stage, attempt=active.attempt),
                )
            if should_pause:
                self.record(
                    EventType.CONTROL,
                    "/pause",
                    status=EventStatus.CONSUMED,
                    agent_kind=active.agent_kind,
                    round_label=active.round_label,
                    execution_id=execution_id,
                )
        if getattr(self._legacy_execution_local, "execution_id", None) == execution_id:
            self._legacy_execution_local.execution_id = None

    def status(self) -> str:  # noqa: D102  # tracked: #288
        with self._condition:
            state = "paused" if self._paused else self._run_status
            kind = self._current_kind or "starting"
            round_label = self._current_round or "no round yet"
        return f"{state} · {kind} · {round_label}"

    def finish(  # noqa: D102  # tracked: #288
        self,
        error: BaseException | None = None,
        *,
        record_event: bool = True,
        diagnostic: Diagnostic | None = None,
    ) -> None:
        with self._condition:
            if self._run_status in {"completed", "failed"}:
                return
            for execution_id, active in tuple(self._active_executions.items()):
                if execution_id not in self._run_control_execution_ids:
                    continue
                message = "Run ended before the agent execution completed"
                self.record(
                    EventType.AGENT_EXECUTION_FINISHED,
                    message,
                    status=EventStatus.INTERRUPTED,
                    data=AgentExecutionFinishedData(error=message),
                    agent_kind=active.agent_kind,
                    round_label=active.round_label,
                    execution_id=execution_id,
                )
                self._active_executions.pop(execution_id, None)
                self._run_control_execution_ids.discard(execution_id)
                self._execution_todo_summaries.pop(execution_id, None)
                self._execution_active_tools.pop(execution_id, None)
                self.record(
                    EventType.INVOCATION_FINISHED,
                    message,
                    status=EventStatus.INTERRUPTED,
                    data=InvocationFinishedData(error=message),
                    agent_kind=active.agent_kind,
                    round_label=active.round_label,
                    execution_id=execution_id,
                )
                self.record(
                    EventType.PHASE_FINISHED,
                    message,
                    status=EventStatus.INTERRUPTED,
                    data=PhaseData(phase=active.stage, attempt=active.attempt),
                    agent_kind=active.agent_kind,
                    round_label=active.round_label,
                    execution_id=execution_id,
                )
            self._run_status = "failed" if error else "completed"
            self._condition.notify_all()

        event_diagnostic = diagnostic
        if error is not None and event_diagnostic is not None:
            # A terminal run failure is fatal, but it may originate at an
            # earlier boundary. Preserve that origin and stable identity so
            # consumers can coalesce the invocation, phase, and run events.
            event_diagnostic = event_diagnostic.model_copy(
                update={"severity": DiagnosticSeverity.FATAL}
            )
        try:
            if not record_event:
                return
            if error is not None and event_diagnostic is None:
                self._record_failure_event(
                    EventType.RUN_FAILED,
                    error,
                    scope=DiagnosticScope.RUN,
                    operation="Run",
                    severity=DiagnosticSeverity.FATAL,
                )
                return
            self.record(
                EventType.RUN_FAILED if error else EventType.RUN_FINISHED,
                event_diagnostic.summary if event_diagnostic else "",
                status=EventStatus.FAILED if error else EventStatus.COMPLETED,
                diagnostic=event_diagnostic,
            )
        finally:
            with self._condition:
                self._error_diagnostics.clear()

    def _diagnostic_for(
        self, error: BaseException, scope: DiagnosticScope, *, operation: str
    ) -> Diagnostic:
        """Return the canonical diagnostic for one exception instance."""
        key = id(error)
        with self._condition:
            for item in _exception_chain(error):
                cached = self._error_diagnostics.get(id(item))
                if cached is None or cached[0] is not item:
                    continue
                if item is error:
                    return cached[1]
                diagnostic = cached[1].model_copy(update={"detail": exception_detail(error)})
                self._error_diagnostics[key] = (error, diagnostic)
                return diagnostic
        diagnostic = exception_to_diagnostic(
            error,
            scope=scope,
            operation=operation,
            severity=DiagnosticSeverity.ERROR,
            retryability=DiagnosticRetryability.UNKNOWN,
        )
        with self._condition:
            self._error_diagnostics[key] = (error, diagnostic)
        return diagnostic


_CHAT_THREAD_TITLE_MAX_CHARS = 40


def _chat_thread_title(question: str) -> str:
    """Title a thread from its first message: first line, cut on a word."""
    line = next((part.strip() for part in question.strip().splitlines() if part.strip()), "")
    if len(line) <= _CHAT_THREAD_TITLE_MAX_CHARS:
        return line
    cut = line[:_CHAT_THREAD_TITLE_MAX_CHARS]
    head, separator, _rest = cut.rpartition(" ")
    return f"{head.rstrip() if separator else cut}…"


def _attempt_from_label(round_label: str) -> int | None:
    match = re.search(r"retry-(\d+)", round_label)
    return int(match.group(1)) if match else None


def _execution_error_status(error: BaseException) -> EventStatus:
    if isinstance(error, asyncio.CancelledError) or type(error).__name__ == "CancelledError":
        return EventStatus.CANCELLED
    if isinstance(error, (KeyboardInterrupt, SystemExit)):
        return EventStatus.INTERRUPTED
    return EventStatus.FAILED


def _initial_activity_summary(kind: str) -> str:
    normalized = kind.lower()
    if "orchestrat" in normalized or "plan" in normalized:
        return "Planning"
    if "implement" in normalized:
        return "Implementing"
    if "judge" in normalized or "review" in normalized:
        return "Reviewing"
    if "profil" in normalized or "benchmark" in normalized:
        return "Profiling"
    if normalized == "chat":
        return "Answering question"
    return f"Running {kind}"


def _text_activity(data: AgentOutputChunkData) -> AgentExecutionActivityData | None:
    if data.channel == "analysis":
        return AgentExecutionActivityData(mode="thinking", summary="Thinking")
    if data.channel == "assistant":
        return AgentExecutionActivityData(mode="responding", summary="Responding")
    return None


def _exception_chain(error: BaseException) -> list[BaseException]:
    """Follow causes and contexts without depending on diagnostic internals."""
    chain: list[BaseException] = []
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and len(chain) < _MAX_EXCEPTION_CHAIN:
        marker = id(current)
        if marker in seen:
            break
        seen.add(marker)
        chain.append(current)
        if current.__cause__ is not None:
            current = current.__cause__
        elif not current.__suppress_context__:
            current = current.__context__
        else:
            current = None
    return chain


def _with_steering(user_prompt: str, messages: list[str]) -> str:
    """Append queued operator steering instructions to an agent's user prompt."""
    if not messages:
        return user_prompt
    block = "\n".join(f"- {message}" for message in messages)
    return (
        f"{user_prompt.rstrip()}\n\n"
        "## Operator steering (live)\n\n"
        "The operator sent the following instruction(s) for this invocation. "
        "Treat them as high-priority guidance for the work you do now:\n\n"
        f"{block}\n"
    )


def _canonical_execution_events(
    events: list[RunEvent],
    *,
    canonical_lifecycle_ids: set[str] | None = None,
    invocation_lifecycle_ids: set[str] | None = None,
) -> list[RunEvent]:
    """Translate persisted legacy lifecycle events without rewriting their log."""
    if canonical_lifecycle_ids is None:
        canonical_lifecycle_ids = {
            event.execution_id
            for event in events
            if event.type in {EventType.AGENT_EXECUTION_STARTED, EventType.AGENT_EXECUTION_FINISHED}
            and event.execution_id is not None
        }
    if invocation_lifecycle_ids is None:
        invocation_lifecycle_ids = {
            event.execution_id
            for event in events
            if event.type in {EventType.INVOCATION_STARTED, EventType.INVOCATION_FINISHED}
            and event.execution_id is not None
        }
    canonical: list[RunEvent] = []
    for event in events:
        if event.execution_id in canonical_lifecycle_ids and event.type in {
            EventType.INVOCATION_STARTED,
            EventType.INVOCATION_FINISHED,
        }:
            continue
        if event.type is EventType.INVOCATION_STARTED and isinstance(
            event.data, InvocationStartedData
        ):
            canonical.append(
                event.model_copy(
                    update={
                        "type": EventType.AGENT_EXECUTION_STARTED,
                        "data": AgentExecutionStartedData(
                            stage=event.agent_kind or "agent",
                            attempt=_attempt_from_label(event.round_label or ""),
                            system_prompt=event.data.system_prompt,
                            user_prompt=event.data.user_prompt,
                            activity=AgentExecutionActivityData(
                                mode="thinking",
                                summary=_initial_activity_summary(event.agent_kind or "agent"),
                            ),
                        ),
                    }
                )
            )
            continue
        if event.type is EventType.INVOCATION_FINISHED and isinstance(
            event.data, InvocationFinishedData
        ):
            canonical.append(
                event.model_copy(
                    update={
                        "type": EventType.AGENT_EXECUTION_FINISHED,
                        "data": AgentExecutionFinishedData(
                            result=event.data.result, error=event.data.error
                        ),
                    }
                )
            )
            continue
        if event.type in {EventType.PHASE_STARTED, EventType.PHASE_FINISHED} and isinstance(
            event.data, PhaseData
        ):
            if (
                event.execution_id is not None
                and event.execution_id not in invocation_lifecycle_ids
                and event.execution_id not in canonical_lifecycle_ids
                and event.type is EventType.PHASE_STARTED
            ):
                canonical.append(
                    event.model_copy(
                        update={
                            "type": EventType.AGENT_EXECUTION_STARTED,
                            "data": AgentExecutionStartedData(
                                stage=event.data.phase,
                                attempt=event.data.attempt,
                                activity=AgentExecutionActivityData(
                                    mode="thinking",
                                    summary=_initial_activity_summary(event.data.phase),
                                ),
                            ),
                        }
                    )
                )
            elif (
                event.execution_id is not None
                and event.execution_id not in invocation_lifecycle_ids
                and event.execution_id not in canonical_lifecycle_ids
            ):
                canonical.append(
                    event.model_copy(
                        update={
                            "type": EventType.AGENT_EXECUTION_FINISHED,
                            "data": AgentExecutionFinishedData(error=event.text or None),
                        }
                    )
                )
            canonical.append(event)
            continue
        canonical.append(event)
    return canonical
