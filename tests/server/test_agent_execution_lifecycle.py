"""Contract tests for authoritative agent-execution activity state."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from vibesys.server.events import (
    AgentExecutionActivityData,
    EventStatus,
    EventStore,
    EventType,
    InvocationFinishedData,
    InvocationStartedData,
    RunEvent,
    TodoItemData,
    TodoUpdateData,
    ToolCallData,
    ToolResultData,
)
from vibesys.server.supervisor import RunSupervisor


def test_explicit_executions_remain_independent_and_finish_idempotently(tmp_path):  # noqa: ANN001, ANN201
    supervisor = RunSupervisor()
    supervisor.attach(tmp_path)

    first = supervisor.start_agent_execution("implementer", "round-1", "first")
    second = supervisor.start_agent_execution("implementer", "round-1-retry-2", "second")

    assert {item.execution_id for item in supervisor.snapshot().active_executions} == {
        first.execution_id,
        second.execution_id,
    }
    supervisor.after_agent("implementer", "round-1", result="done", execution_id=first.execution_id)
    supervisor.after_agent(
        "implementer", "round-1", result="ignored", execution_id=first.execution_id
    )
    assert [item.execution_id for item in supervisor.snapshot().active_executions] == [
        second.execution_id
    ]

    events = supervisor.read_events()
    assert sum(event.type is EventType.AGENT_EXECUTION_STARTED for event in events) == 2
    assert sum(event.type is EventType.AGENT_EXECUTION_FINISHED for event in events) == 1
    assert all(
        event.type
        not in {
            EventType.INVOCATION_STARTED,
            EventType.INVOCATION_FINISHED,
        }
        for event in events
    )
    assert sum(event.type is EventType.PHASE_STARTED for event in events) == 2
    first_start = next(
        event
        for event in events
        if event.type is EventType.AGENT_EXECUTION_STARTED
        and event.execution_id == first.execution_id
    )
    assert not any(
        event.type is EventType.AGENT_EXECUTION_STARTED and event.execution_id == first.execution_id
        for event in supervisor.read_events(first_start.sequence)
    )


def test_start_agent_execution_records_runtime_identity_when_provided(tmp_path):  # noqa: ANN001, ANN201
    supervisor = RunSupervisor()
    supervisor.attach(tmp_path)

    supervisor.start_agent_execution(
        "implementer",
        "round-1",
        "work",
        driver="agentshim",
        provider="codex",
        model="gpt-5.1-codex-max",
    )

    started = next(
        event
        for event in supervisor.read_events()
        if event.type is EventType.AGENT_EXECUTION_STARTED
    )
    assert started.data is not None
    assert started.data.kind == "agent_execution_started"
    assert started.data.driver == "agentshim"
    assert started.data.provider == "codex"
    assert started.data.model == "gpt-5.1-codex-max"


def test_start_agent_execution_omits_runtime_identity_by_default(tmp_path):  # noqa: ANN001, ANN201
    supervisor = RunSupervisor()
    supervisor.attach(tmp_path)

    supervisor.start_agent_execution("implementer", "round-1", "work")

    started = next(
        event
        for event in supervisor.read_events()
        if event.type is EventType.AGENT_EXECUTION_STARTED
    )
    assert started.data is not None
    assert started.data.kind == "agent_execution_started"
    assert started.data.driver is None
    assert started.data.provider is None
    assert started.data.model is None


def test_active_execution_checkpoint_carries_runtime_identity(tmp_path):  # noqa: ANN001, ANN201
    """The snapshot/checkpoint record must match the live event's identity.

    A TUI reconnecting mid-round, or reconciling a periodic subscription
    checkpoint, reads this record instead of the agent_execution_started
    event: if it dropped driver/provider/model, the harness+model label would
    disappear from any round whose checkpoint arrived after the round's own
    start event.
    """
    supervisor = RunSupervisor()
    supervisor.attach(tmp_path)

    supervisor.start_agent_execution(
        "implementer",
        "round-1",
        "work",
        driver="agentshim",
        provider="codex",
        model="gpt-5.1-codex-max",
    )

    active = supervisor.snapshot().active_executions
    assert len(active) == 1
    assert active[0].driver == "agentshim"
    assert active[0].provider == "codex"
    assert active[0].model == "gpt-5.1-codex-max"

    _, _, checkpointed = supervisor.subscription_checkpoint(0)
    assert len(checkpointed) == 1
    assert checkpointed[0].driver == "agentshim"
    assert checkpointed[0].provider == "codex"
    assert checkpointed[0].model == "gpt-5.1-codex-max"


def test_active_execution_checkpoint_omits_runtime_identity_by_default(tmp_path):  # noqa: ANN001, ANN201
    supervisor = RunSupervisor()
    supervisor.attach(tmp_path)

    supervisor.start_agent_execution("implementer", "round-1", "work")

    active = supervisor.snapshot().active_executions
    assert active[0].driver is None
    assert active[0].provider is None
    assert active[0].model is None


def test_activity_tracks_todo_and_parallel_tools(tmp_path):  # noqa: ANN001, ANN201
    supervisor = RunSupervisor()
    supervisor.attach(tmp_path)
    execution = supervisor.start_agent_execution("implementer", "round-1", "work")

    supervisor.publish_presentation(
        EventType.TODO_UPDATE,
        TodoUpdateData(todos=[TodoItemData(content="Run queue tests", status="in_progress")]),
        invocation_id=execution.execution_id,
    )
    supervisor.publish_presentation(
        EventType.TOOL_CALL,
        ToolCallData(tool="Bash", args={}),
        invocation_id=execution.execution_id,
    )
    supervisor.publish_presentation(
        EventType.TOOL_CALL,
        ToolCallData(tool="Read", args={}),
        invocation_id=execution.execution_id,
    )
    supervisor.publish_presentation(
        EventType.TOOL_RESULT,
        ToolResultData(tool="Read", content="ok"),
        invocation_id=execution.execution_id,
    )
    assert supervisor.snapshot().active_executions[0].activity.tool == "Bash"

    supervisor.publish_presentation(
        EventType.TOOL_RESULT,
        ToolResultData(tool="Bash", content="ok"),
        invocation_id=execution.execution_id,
    )
    activity = supervisor.snapshot().active_executions[0].activity
    assert activity == AgentExecutionActivityData(mode="thinking", summary="Run queue tests")


@pytest.mark.parametrize("terminal_todo_status", ["pending", "completed"])
def test_todo_without_in_progress_item_clears_stale_summary(tmp_path, terminal_todo_status):  # noqa: ANN001, ANN201
    supervisor = RunSupervisor()
    supervisor.attach(tmp_path)
    execution = supervisor.start_agent_execution("implementer", "round-1", "work")
    supervisor.publish_presentation(
        EventType.TODO_UPDATE,
        TodoUpdateData(todos=[TodoItemData(content="Run tests", status="in_progress")]),
        invocation_id=execution.execution_id,
    )

    supervisor.publish_presentation(
        EventType.TODO_UPDATE,
        TodoUpdateData(todos=[TodoItemData(content="Run tests", status=terminal_todo_status)]),
        invocation_id=execution.execution_id,
    )

    assert supervisor.snapshot().active_executions[0].activity == AgentExecutionActivityData(
        mode="thinking", summary="Thinking"
    )


def test_todo_without_in_progress_item_preserves_active_tool(tmp_path):  # noqa: ANN001, ANN201
    supervisor = RunSupervisor()
    supervisor.attach(tmp_path)
    execution = supervisor.start_agent_execution("implementer", "round-1", "work")
    supervisor.publish_presentation(
        EventType.TOOL_CALL,
        ToolCallData(tool="Bash", args={}),
        invocation_id=execution.execution_id,
    )

    supervisor.publish_presentation(
        EventType.TODO_UPDATE,
        TodoUpdateData(todos=[TodoItemData(content="Run tests", status="completed")]),
        invocation_id=execution.execution_id,
    )

    assert supervisor.snapshot().active_executions[0].activity == AgentExecutionActivityData(
        mode="tool", summary="Using Bash", tool="Bash"
    )


def test_checkpoint_watermark_and_active_state_are_consistent(tmp_path):  # noqa: ANN001, ANN201
    supervisor = RunSupervisor()
    supervisor.attach(tmp_path)
    execution = supervisor.start_agent_execution("judge", "round-2", "review")

    through_sequence, events, active = supervisor.subscription_checkpoint(0)

    assert all(event.sequence <= through_sequence for event in events)
    assert [item.execution_id for item in active] == [execution.execution_id]
    assert active[0].activity.summary == "Reviewing"
    started = next(event for event in events if event.type is EventType.AGENT_EXECUTION_STARTED)
    assert started.data is not None
    assert started.data.kind == "agent_execution_started"
    assert started.data.activity == active[0].activity
    supervisor.after_agent("judge", "round-2", execution_id=execution.execution_id)
    through_sequence, events, active = supervisor.subscription_checkpoint(through_sequence)
    assert events[-1].sequence == through_sequence
    assert active == []


def test_resume_moves_bootstrap_events_into_durable_subscription_history(tmp_path):  # noqa: ANN001, ANN201
    """A resumed TUI must replay prior rounds and the current process together."""
    durable_dir = tmp_path / "durable"
    durable_dir.mkdir()
    legacy_execution_id = "a" * 32
    durable = EventStore(durable_dir / "run-events.jsonl", "run-1")
    durable.append(
        RunEvent(
            timestamp=datetime.now(UTC),
            type=EventType.INVOCATION_STARTED,
            status=EventStatus.ACTIVE,
            agent_kind="implementer",
            round_label="round-1-implementer",
            invocation_id=legacy_execution_id,
            data=InvocationStartedData(system_prompt="system", user_prompt="prior work"),
        )
    )

    supervisor = RunSupervisor()
    supervisor.attach(tmp_path / "bootstrap")
    supervisor.publish_agent_output(
        "bootstrap work",
        agent_kind="orchestrator",
        round_label="round-2-plan",
    )
    supervisor.attach(durable_dir)
    supervisor.publish_agent_output(
        "current work",
        agent_kind="implementer",
        round_label="round-2-implementer",
    )

    through_sequence, events, _active = supervisor.subscription_checkpoint(0)

    assert [event.sequence for event in events] == list(range(1, through_sequence + 1))
    assert any(
        event.type is EventType.AGENT_EXECUTION_STARTED
        and event.execution_id == legacy_execution_id
        and event.round_label == "round-1-implementer"
        for event in events
    )
    output = [
        event.data.content
        for event in events
        if event.data is not None and event.data.kind == "agent_output_chunk"
    ]
    assert output == ["bootstrap work", "current work"]
    assert supervisor.read_history_events() == events


def test_streamed_text_does_not_override_active_tool(tmp_path):  # noqa: ANN001, ANN201
    supervisor = RunSupervisor()
    supervisor.attach(tmp_path)
    execution = supervisor.start_agent_execution("implementer", "round-1", "work")
    supervisor.publish_presentation(
        EventType.TOOL_CALL,
        ToolCallData(tool="Bash", args={}),
        invocation_id=execution.execution_id,
    )

    supervisor.publish_agent_output("still working", invocation_id=execution.execution_id)

    assert supervisor.snapshot().active_executions[0].activity == AgentExecutionActivityData(
        mode="tool", summary="Using Bash", tool="Bash"
    )


def test_chat_execution_is_isolated_from_main_run_control(tmp_path):  # noqa: ANN001, ANN201
    supervisor = RunSupervisor()
    supervisor.attach(tmp_path)
    main = supervisor.start_agent_execution("implementer", "round-1", "work")
    supervisor.pause_after_call()
    chat = supervisor.start_agent_execution(
        "chat",
        "experiment-chat",
        "status?",
        consume_steering=False,
        participates_in_run_control=False,
    )

    assert supervisor.snapshot().agent_kind == "implementer"
    assert supervisor.snapshot().round_label == "round-1"
    supervisor.after_agent("chat", "experiment-chat", execution_id=chat.execution_id)
    assert supervisor.snapshot().status == "running"
    assert supervisor.snapshot().agent_kind == "implementer"

    supervisor.after_agent("implementer", "round-1", execution_id=main.execution_id)
    assert supervisor.snapshot().status == "paused"
    paused_chat = supervisor.start_agent_execution(
        "chat",
        "experiment-chat",
        "status?",
        consume_steering=False,
        participates_in_run_control=False,
    )
    supervisor.after_agent("chat", "experiment-chat", execution_id=paused_chat.execution_id)
    assert supervisor.snapshot().status == "paused"
    assert supervisor.snapshot().agent_kind == "implementer"


def test_cancellation_and_run_finish_terminalize_activity(tmp_path):  # noqa: ANN001, ANN201
    supervisor = RunSupervisor()
    supervisor.attach(tmp_path)
    cancelled = supervisor.start_agent_execution("implementer", "round-1", "work")
    supervisor.after_agent(
        "implementer",
        "round-1",
        error=asyncio.CancelledError(),
        execution_id=cancelled.execution_id,
    )
    dangling = supervisor.start_agent_execution("judge", "round-1", "review")
    supervisor.finish()

    terminal = {
        event.execution_id: event.status
        for event in supervisor.read_events()
        if event.type is EventType.AGENT_EXECUTION_FINISHED
    }
    assert terminal[cancelled.execution_id] is EventStatus.CANCELLED
    assert terminal[dangling.execution_id] is EventStatus.INTERRUPTED
    assert supervisor.snapshot().active_executions == []


def test_legacy_invocation_log_is_projected_without_becoming_live(tmp_path):  # noqa: ANN001, ANN201
    execution_id = "a" * 32
    store = EventStore(tmp_path / "run-events.jsonl", "legacy")
    store.append(
        RunEvent(
            timestamp=datetime.now(UTC),
            type=EventType.INVOCATION_STARTED,
            status=EventStatus.ACTIVE,
            agent_kind="implementer",
            round_label="round-1",
            invocation_id=execution_id,
            data=InvocationStartedData(system_prompt="system", user_prompt="work"),
        )
    )

    supervisor = RunSupervisor()
    supervisor.attach(tmp_path)

    event = next(
        event
        for event in supervisor.read_events()
        if event.type is EventType.AGENT_EXECUTION_STARTED
    )
    assert event.execution_id == execution_id
    assert event.data is not None
    assert event.data.kind == "agent_execution_started"
    assert supervisor.snapshot().active_executions == []

    # A legacy terminal event remains readable through the same projection.
    store = supervisor._store  # noqa: SLF001
    assert store is not None
    store.append(
        RunEvent(
            timestamp=datetime.now(UTC),
            type=EventType.INVOCATION_FINISHED,
            status=EventStatus.COMPLETED,
            agent_kind="implementer",
            round_label="round-1",
            invocation_id=execution_id,
            data=InvocationFinishedData(result="done"),
        )
    )
    assert supervisor.read_events()[-1].type is EventType.AGENT_EXECUTION_FINISHED


def test_failed_lifecycle_append_does_not_advance_active_state(tmp_path, monkeypatch):  # noqa: ANN001, ANN201
    supervisor = RunSupervisor()
    supervisor.attach(tmp_path)
    store = supervisor._store  # noqa: SLF001
    assert store is not None
    append = store.append

    def fail_start(event):  # noqa: ANN001, ANN202
        if event.type is EventType.AGENT_EXECUTION_STARTED:
            raise OSError("disk full")  # noqa: TRY003
        return append(event)

    monkeypatch.setattr(store, "append", fail_start)
    with pytest.raises(OSError, match="disk full"):
        supervisor.start_agent_execution("implementer", "round-1", "work")
    assert supervisor.snapshot().active_executions == []

    monkeypatch.setattr(store, "append", append)
    execution = supervisor.start_agent_execution("implementer", "round-1", "work")

    def fail_finish(event):  # noqa: ANN001, ANN202
        if event.type is EventType.AGENT_EXECUTION_FINISHED:
            raise OSError("disk full")  # noqa: TRY003
        return append(event)

    monkeypatch.setattr(store, "append", fail_finish)
    with pytest.raises(OSError, match="disk full"):
        supervisor.after_agent("implementer", "round-1", execution_id=execution.execution_id)
    assert [item.execution_id for item in supervisor.snapshot().active_executions] == [
        execution.execution_id
    ]
