"""Pause, steering, and terminal-state tests for the run controller."""

import threading
import time

from tests.server.support import build_server_parts

from server.api.protocol import PauseCommand, ResumeCommand, SteerCommand
from server.events import AgentExecutionStartedData, EventStatus, EventType


def test_pause_takes_effect_at_next_safe_point(tmp_path):  # noqa: ANN001, ANN201
    parts = build_server_parts(tmp_path)
    execution = parts.controller.start_agent_execution("implementer", "round 1", "work")
    parts.controller.pause_after_call()
    parts.controller.after_agent("implementer", "round 1", execution_id=execution.execution_id)

    result: list[str] = []
    waiter = threading.Thread(
        target=lambda: result.append(parts.controller.before_agent("judge", "round 1", "prompt"))
    )
    waiter.start()
    time.sleep(0.02)
    assert waiter.is_alive()
    parts.controller.resume()
    waiter.join(timeout=1)
    assert result == ["prompt"]


def test_steering_is_injected_once(tmp_path):  # noqa: ANN001, ANN201
    parts = build_server_parts(tmp_path)
    parts.controller.steer("focus on the KV cache")

    effective = parts.controller.before_agent("implementer", "round 1", "Do the work")

    assert "Do the work" in effective
    assert "focus on the KV cache" in effective
    assert "Operator steering" in effective
    started = next(
        event for event in parts.journal.read() if event.type is EventType.AGENT_EXECUTION_STARTED
    )
    assert isinstance(started.data, AgentExecutionStartedData)
    assert started.data.user_prompt == effective

    parts.controller.after_agent("implementer", "round 1")
    assert parts.controller.before_agent("judge", "round 1", "Review it") == "Review it"


def test_steering_queued_while_paused_applies_on_resume(tmp_path):  # noqa: ANN001, ANN201
    parts = build_server_parts(tmp_path)
    execution = parts.controller.start_agent_execution("implementer", "round 1", "work")
    parts.controller.pause_after_call()
    parts.controller.after_agent("implementer", "round 1", execution_id=execution.execution_id)

    result: list[str] = []
    waiter = threading.Thread(
        target=lambda: result.append(parts.controller.before_agent("judge", "round 1", "Review"))
    )
    waiter.start()
    time.sleep(0.02)
    parts.controller.steer("check for reward hacking")
    parts.controller.resume()
    waiter.join(timeout=1)

    assert len(result) == 1
    assert "Review" in result[0]
    assert "check for reward hacking" in result[0]


def test_api_control_commands_ack_and_reach_controller(tmp_path):  # noqa: ANN001, ANN201
    parts = build_server_parts(tmp_path)

    pause = parts.api.execute(PauseCommand())
    resume = parts.api.execute(ResumeCommand())
    steer = parts.api.execute(SteerCommand(text="prioritize latency"))

    assert pause.ack is not None
    assert resume.ack is not None
    assert steer.ack is not None
    assert (pause.ack.action, pause.ack.status) == ("pause", "pending")
    assert (resume.ack.action, resume.ack.status) == ("resume", "consumed")
    assert (steer.ack.action, steer.ack.status) == ("steer", "pending")
    assert "prioritize latency" in parts.controller.before_agent("implementer", "round 1", "Work")


def test_finish_is_idempotent_and_interrupts_controlled_executions(tmp_path):  # noqa: ANN001, ANN201
    parts = build_server_parts(tmp_path)
    execution = parts.controller.start_agent_execution("implementer", "round 1", "work")

    parts.controller.finish(RuntimeError("first failure"))
    parts.controller.finish(RuntimeError("second failure"))

    failed = [event for event in parts.journal.read() if event.type is EventType.RUN_FAILED]
    assert len(failed) == 1
    assert failed[0].diagnostic is not None
    assert failed[0].diagnostic.detail == "RuntimeError: first failure"
    finished = next(
        event
        for event in parts.journal.read()
        if event.type is EventType.AGENT_EXECUTION_FINISHED
        and event.execution_id == execution.execution_id
    )
    assert finished.status is EventStatus.INTERRUPTED


def test_finish_does_not_interrupt_presentation_only_chat(tmp_path):  # noqa: ANN001, ANN201
    parts = build_server_parts(tmp_path)
    execution = parts.controller.start_agent_execution(
        "chat",
        "experiment-chat",
        "what happened?",
        participates_in_run_control=False,
    )

    parts.controller.finish()

    assert [active.execution_id for active in parts.api.snapshot().active_executions] == [
        execution.execution_id
    ]
    parts.controller.after_agent(
        "chat", "experiment-chat", result="answer", execution_id=execution.execution_id
    )
    finished = [
        event
        for event in parts.journal.read()
        if event.type is EventType.AGENT_EXECUTION_FINISHED
        and event.execution_id == execution.execution_id
    ]
    assert len(finished) == 1
    assert finished[0].status is EventStatus.COMPLETED
