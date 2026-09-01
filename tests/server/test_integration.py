"""Tests for the adapter between core run ports and server components."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, cast

import pytest
from tests.server.support import build_server_parts

from server.api.protocol import ChatQuery, ChatThreadCreateQuery
from server.chat.factory import ChatAgentResources
from server.events import EventType
from vibesys.run.events import (
    AgentOutputChunkData,
    CoreEventType,
    ToolCallData,
)
from vibesys.run.integration import (
    AgentSelection,
    RunAttachment,
)
from vs_project import AgentRunConfiguration, Project, RunEnvironmentRecord

if TYPE_CHECKING:
    from pathlib import Path


class _ChatClient:
    """Record chat invocations and return a deterministic answer."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def invoke_text(self, **kwargs: Any) -> str:  # noqa: ANN401
        self.calls.append(kwargs)
        return "It improved in round 2."


def _project_run(root: Path) -> tuple[Project, str]:
    root.mkdir()
    (root / "OBJECTIVE.md").write_text("Make the queue fast.\n", encoding="utf-8")
    project = Project.open(root)
    project.state.create_project("queue")
    manifest = project.state.new_run_manifest(
        "queue",
        run_id="queue-run",
        branch="vibesys/queue-run",
        vibesys_version="0.2.0-test",
        configuration=AgentRunConfiguration(
            outer_loop="agent",
            run_environment=RunEnvironmentRecord(name="local"),
            inner_loop="single-agent",
            interface="inprocess",
            agent_backend="cli",
            compute_backend="cpu",
            profiler="none",
            max_rounds=3,
            max_retries_per_round=1,
            judge_every=1,
            official_eval_every=1,
            memory_layout="files",
        ),
        trusted_input_baseline="0" * 40,
    )
    project.state.create_run(manifest)
    return project, manifest.run_id


def test_core_events_project_to_wire_journal_and_execution_activity(tmp_path):  # noqa: ANN001, ANN201
    parts = build_server_parts(tmp_path)
    handle = parts.integration.invocations.start(
        "implementer", "round-1", "work", driver="agentshim", provider="codex"
    )
    assert handle.execution_id is not None

    parts.integration.events.emit(
        CoreEventType.TOOL_CALL,
        agent_kind="implementer",
        round_label="round-1",
        execution_id=handle.execution_id,
        data=ToolCallData(tool="Bash", args={}),
    )
    parts.integration.events.emit(
        CoreEventType.AGENT_OUTPUT_CHUNK,
        agent_kind="implementer",
        round_label="round-1",
        execution_id=handle.execution_id,
        data=AgentOutputChunkData(channel="assistant", content="working"),
    )

    assert [
        event.type
        for event in parts.journal.read()
        if event.type in {EventType.TOOL_CALL, EventType.AGENT_OUTPUT_CHUNK}
    ] == [EventType.TOOL_CALL, EventType.AGENT_OUTPUT_CHUNK]
    activity = parts.api.snapshot().active_executions[0].activity
    assert activity.mode == "tool"
    assert activity.tool == "Bash"
    assert (tmp_path / "core-events.jsonl").is_file()


def test_invocation_adapter_applies_steering_without_emitting_duplicate_lifecycle(
    tmp_path: Path,
) -> None:
    parts = build_server_parts(tmp_path)
    parts.controller.steer("measure latency first")

    handle = parts.integration.invocations.start("implementer", "round-1", "work")

    assert "measure latency first" in handle.user_prompt
    assert not any(
        event.type is EventType.AGENT_EXECUTION_STARTED for event in parts.journal.read()
    )
    assert len(parts.api.snapshot().active_executions) == 1
    parts.integration.invocations.finish(
        "implementer", "round-1", result="done", execution_id=handle.execution_id
    )
    assert parts.api.snapshot().active_executions == []


def test_attach_run_installs_chat_with_isolated_session_state(tmp_path):  # noqa: ANN001, ANN201
    project, run_id = _project_run(tmp_path / "project")
    client = _ChatClient()
    closed: list[str] = []

    def build_agent(
        _attachment: RunAttachment,
        selection: AgentSelection,
        thread_id: str | None,
        shared_state_dir: Path,
    ) -> ChatAgentResources:
        assert selection == AgentSelection(driver="agentshim", provider="codex", model="gpt-test")
        assert thread_id is None
        return ChatAgentResources(
            client=client,
            close=lambda: closed.append("closed"),
            log=lambda _message: None,
            flush_logs=lambda: None,
            environment=dict,
            progress=lambda: None,
            agent_shared_state_dir=str(shared_state_dir),
        )

    parts = build_server_parts(chat_agent_builder=build_agent)
    detach = parts.integration.attach_run(
        RunAttachment(
            project=project,
            run_id=run_id,
            workspace=project.root,
            log_dir=project.state.log_directory(run_id),
            agent_backend="cli",
            agent_defaults=AgentSelection(driver="agentshim", provider="codex", model="gpt-test"),
            agent_runtime=cast("Any", None),
        )
    )
    assert detach is not None

    response = parts.api.execute(ChatQuery(text="what improved?"))

    assert response.chat is not None
    assert response.chat.answer == "It improved in round 2."
    assert client.calls[0]["reuse_session"] is False
    assert client.calls[0]["user_prompt"] == "what improved?"
    transcript = project.state.log_directory(run_id).parent / "server/chat/conversation.jsonl"
    assert json.loads(transcript.read_text()) == {
        "question": "what improved?",
        "answer": "It improved in round 2.",
    }
    assert not (project.root / ".vibesys/server").exists()
    assert str(transcript.parent) in client.calls[0]["system_prompt"]
    detach()
    assert closed == ["closed"]


def test_non_cli_run_rejects_new_chat_threads(tmp_path):  # noqa: ANN001, ANN201
    project, run_id = _project_run(tmp_path / "project")
    client = _ChatClient()

    def build_agent(
        _attachment: RunAttachment,
        _selection: AgentSelection,
        _thread_id: str | None,
        shared_state_dir: Path,
    ) -> ChatAgentResources:
        return ChatAgentResources(
            client=client,
            close=lambda: None,
            log=lambda _message: None,
            flush_logs=lambda: None,
            environment=dict,
            progress=lambda: None,
            agent_shared_state_dir=str(shared_state_dir),
        )

    parts = build_server_parts(chat_agent_builder=build_agent)
    parts.integration.attach_run(
        RunAttachment(
            project=project,
            run_id=run_id,
            workspace=project.root,
            log_dir=project.state.log_directory(run_id),
            agent_backend="stub",
            agent_defaults=AgentSelection(driver="agentshim", provider="codex", model="gpt-test"),
            agent_runtime=cast("Any", None),
        )
    )

    with pytest.raises(ValueError, match="require the CLI agent backend"):
        parts.api.execute(ChatThreadCreateQuery(provider="codex", model="gpt-test"))


def test_close_is_idempotent_and_stops_event_projection(tmp_path):  # noqa: ANN001, ANN201
    parts = build_server_parts(tmp_path)
    parts.integration.close()
    parts.integration.close()

    parts.integration.events.emit(
        CoreEventType.AGENT_OUTPUT_CHUNK,
        data=AgentOutputChunkData(channel="assistant", content="after close"),
    )

    assert not any(event.type is EventType.AGENT_OUTPUT_CHUNK for event in parts.journal.read())
