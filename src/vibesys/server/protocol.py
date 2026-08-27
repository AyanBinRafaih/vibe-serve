"""Versioned transport-neutral contracts for supervision clients."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat

from vibesys.server.diagnostics import Diagnostic, DiagnosticScope, exception_to_diagnostic
from vibesys.server.events import AgentExecutionActivityData, RunEvent

PROTOCOL_VERSION = 1


class ProtocolModel(BaseModel):  # noqa: D101  # tracked: #288
    model_config = ConfigDict(extra="forbid")


class Request(ProtocolModel):  # noqa: D101  # tracked: #288
    protocol_version: Literal[1] = PROTOCOL_VERSION
    request_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


class PauseCommand(Request):  # noqa: D101  # tracked: #288
    type: Literal["command.pause"] = "command.pause"
    mode: Literal["after_current_agent_call"] = "after_current_agent_call"


class ResumeCommand(Request):  # noqa: D101  # tracked: #288
    type: Literal["command.resume"] = "command.resume"


class SteerCommand(Request):  # noqa: D101  # tracked: #288
    type: Literal["command.steer"] = "command.steer"
    text: str = Field(min_length=1)


class SnapshotQuery(Request):  # noqa: D101  # tracked: #288
    type: Literal["query.snapshot"] = "query.snapshot"


class ChatQuery(Request):  # noqa: D101  # tracked: #288
    type: Literal["query.chat"] = "query.chat"
    text: str
    # None targets the default thread, preserving pre-thread clients.
    thread_id: str | None = None


class ChatThreadCreateQuery(Request):
    """Create a new experiment-chat thread with its own agent selection.

    Omitted fields resolve to the run's configured driver, provider, and
    model. The response carries the resolved settings and thread identity.
    ``driver`` exists for completeness and stays validated when supplied, but
    which driver backs a run is a deployment detail: clients omit it so every
    thread inherits the run's.
    """

    type: Literal["query.chat_thread_create"] = "query.chat_thread_create"
    driver: Literal["agentshim", "omnigent"] | None = None
    provider: str | None = None
    model: str | None = None
    # Without a title the server derives one from the thread's first message.
    title: str | None = None


class ChatOptionsQuery(Request):
    """Request the agent selections this run's experiment chat offers."""

    type: Literal["query.chat_options"] = "query.chat_options"


class HistoryQuery(Request):  # noqa: D101  # tracked: #288
    type: Literal["query.history"] = "query.history"


class PerformanceQuery(Request):  # noqa: D101  # tracked: #288
    type: Literal["query.performance"] = "query.performance"


class ExperimentQuery(Request):
    """Request the hypothesis-level experiment log for the attached run."""

    type: Literal["query.experiments"] = "query.experiments"


class EventsQuery(Request):  # noqa: D101  # tracked: #288
    type: Literal["query.events"] = "query.events"
    after_sequence: int = Field(default=0, ge=0)
    timeout_ms: int = Field(default=0, ge=0, le=30_000)


class SubscribeRequest(Request):  # noqa: D101  # tracked: #288
    type: Literal["subscribe"] = "subscribe"
    after_sequence: int = Field(default=0, ge=0)


ProtocolRequest = Annotated[
    PauseCommand
    | ResumeCommand
    | SteerCommand
    | SnapshotQuery
    | ChatQuery
    | ChatThreadCreateQuery
    | ChatOptionsQuery
    | HistoryQuery
    | PerformanceQuery
    | ExperimentQuery
    | EventsQuery
    | SubscribeRequest,
    Field(discriminator="type"),
]


class ActiveAgentExecution(ProtocolModel):
    """Authoritative activity checkpoint for one running agent execution."""

    execution_id: str
    agent_kind: str
    round_label: str
    stage: str
    attempt: int | None = None
    assignment: str
    started_at: datetime
    activity: AgentExecutionActivityData


class RunSnapshot(ProtocolModel):  # noqa: D101  # tracked: #288
    protocol_version: Literal[1] = PROTOCOL_VERSION
    run_id: str
    sequence: int
    status: str
    agent_kind: str | None = None
    round_label: str | None = None
    active_executions: list[ActiveAgentExecution] = Field(default_factory=list)


class CommandAck(ProtocolModel):  # noqa: D101  # tracked: #288
    action: Literal["pause", "resume", "steer"]
    status: Literal["pending", "consumed"]


class ChatResult(ProtocolModel):  # noqa: D101  # tracked: #288
    question: str
    answer: str
    effect: Literal["none"] = "none"
    # Echoes the requested thread; None is the default thread.
    thread_id: str | None = None


class ChatThreadInfo(ProtocolModel):
    """Resolved identity and agent settings of one experiment-chat thread."""

    thread_id: str
    title: str = ""
    driver: str
    provider: str
    model: str


class ChatModelOption(ProtocolModel):
    """One model a chat thread can be started with, and where it came from.

    ``source`` is provenance, not presentation: ``run`` is the run's own
    configured model, ``role`` an ``[agent.outer]``/``[agent.inner]``
    override, ``suggested`` an entry from the backend's short curated list.
    """

    model: str
    source: Literal["run", "role", "suggested"]
    # True for exactly one option across the whole response: the run's model.
    default: bool = False


class ChatProviderOptions(ProtocolModel):
    """One CLI provider the run's configured driver supports, with models."""

    provider: str
    models: list[ChatModelOption] = Field(default_factory=list)


class ChatOptions(ProtocolModel):
    """Every chat agent selection this run offers, grouped by provider.

    The agent driver is absent by design. Threads inherit the run's driver, so
    a client never chooses one and never enumerates them.
    """

    providers: list[ChatProviderOptions] = Field(default_factory=list)


class PerformanceRound(ProtocolModel):  # noqa: D101  # tracked: #288
    round: int
    perf_metric: FiniteFloat
    perf_unit: str
    passed: bool
    profile_skipped: bool = False


class HypothesisRound(ProtocolModel):
    """One round belonging to a hypothesis, for the experiment-log drill-down."""

    round: int
    passed: bool
    reviewed: bool
    hypothesis_outcome: str | None = None
    perf_metric: FiniteFloat | None = None
    perf_unit: str | None = None
    commit: str | None = None
    official_evaluation: bool = False
    candidate_disposition: str | None = None


class HypothesisEntry(ProtocolModel):
    """One unit of investigation: a hypothesis and every round it spans.

    ``resolved_outcome`` is copied from the backend's typed hypothesis state,
    never recomputed by the server or client.
    """

    hypothesis_id: str
    # False when the underlying records carry no ``hypothesis_id``, e.g. a log
    # directory written before hypothesis tracking. The row is still returned
    # so history stays complete; clients render it as an explicit placeholder.
    identified: bool = True
    # Backend-derived display title: the orchestrator's own title, or a
    # fallback derived from ``claim`` when the orchestrator gave none. None
    # when there is no text to title at all.
    title: str | None = None
    claim: str | None = None
    action: str | None = None
    first_round: int
    last_round: int
    rounds: list[HypothesisRound] = Field(default_factory=list)
    resolved_outcome: str | None = None
    # Independent review from the authoritative hypothesis state.
    judge_verdict: Literal["pass", "fail"] | None = None
    perf_metric: FiniteFloat | None = None
    perf_unit: str | None = None
    # Causal delta paired with ``perf_metric`` by the hypothesis state. None
    # means no official comparison is available.
    perf_delta_pct: FiniteFloat | None = None
    # Integration, not truth: the framework's explicit retention decision.
    # None means legacy or not yet assessed, never "official evaluation ran".
    kept: bool | None = None
    # Orchestrator strategy is separate from empirical resolution and
    # candidate retention. It is structured backend state, not roadmap prose.
    strategy_disposition: Literal["available", "parked", "abandoned"] | None = None
    strategy_reason: str | None = None
    active: bool = False


class Response(ProtocolModel):  # noqa: D101  # tracked: #288
    protocol_version: Literal[1] = PROTOCOL_VERSION
    request_id: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    ok: bool = True
    error: str | None = None
    diagnostic: Diagnostic | None = None
    ack: CommandAck | None = None
    chat: ChatResult | None = None
    chat_thread: ChatThreadInfo | None = None
    # None means the run has not attached its agent selection yet, which is
    # distinct from a run that offers no provider at all.
    chat_options: ChatOptions | None = None
    snapshot: RunSnapshot | None = None
    events: list[RunEvent] = Field(default_factory=list)
    performance: list[PerformanceRound] = Field(default_factory=list)
    experiments: list[HypothesisEntry] = Field(default_factory=list)
    # False means canonical project/run state is not attached yet. Keeping the
    # readiness marker separate preserves the protocol-v1 list contract while
    # distinguishing bootstrap from an authoritative empty experiment log.
    experiments_ready: bool | None = None

    @classmethod
    def from_exception(
        cls,
        request_id: str,
        error: BaseException,
        *,
        operation: str = "Request",
        scope: DiagnosticScope = DiagnosticScope.REQUEST,
        code: str | None = None,
    ) -> Response:
        """Build a failed response with consistent legacy and typed errors."""
        diagnostic = exception_to_diagnostic(error, scope=scope, operation=operation, code=code)
        return cls(request_id=request_id, ok=False, error=diagnostic.summary, diagnostic=diagnostic)


class SubscribedMessage(ProtocolModel):  # noqa: D101  # tracked: #288
    type: Literal["subscribed"] = "subscribed"
    request_id: str
    run_id: str
    latest_sequence: int


class EventMessage(ProtocolModel):  # noqa: D101  # tracked: #288
    type: Literal["event"] = "event"
    event: RunEvent


class EventBatchMessage(ProtocolModel):  # noqa: D101  # tracked: #288
    type: Literal["event_batch"] = "event_batch"
    events: list[RunEvent]
    through_sequence: int = Field(default=0, ge=0)
    active_executions: list[ActiveAgentExecution] = Field(default_factory=list)


class ProtocolErrorMessage(ProtocolModel):  # noqa: D101  # tracked: #288
    type: Literal["protocol_error"] = "protocol_error"
    request_id: str | None = None
    code: str
    message: str
    diagnostic: Diagnostic | None = None

    @classmethod
    def from_exception(
        cls,
        error: BaseException,
        *,
        request_id: str | None = None,
        operation: str = "Protocol operation",
        scope: DiagnosticScope = DiagnosticScope.PROTOCOL,
        code: str | None = None,
    ) -> ProtocolErrorMessage:
        """Build a protocol error with consistent legacy and typed errors."""
        diagnostic = exception_to_diagnostic(error, scope=scope, operation=operation, code=code)
        return cls(
            request_id=request_id,
            code=diagnostic.code,
            message=diagnostic.summary,
            diagnostic=diagnostic,
        )


ServerMessage = Annotated[
    SubscribedMessage | EventMessage | EventBatchMessage | ProtocolErrorMessage,
    Field(discriminator="type"),
]
