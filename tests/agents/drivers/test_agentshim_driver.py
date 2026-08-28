from __future__ import annotations

import concurrent.futures
import threading
from dataclasses import dataclass, field
from datetime import timedelta
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, Protocol

import pytest

from vibesys.agents.contracts import (
    AgentEvent,
    AgentExecutionPolicy,
    AgentSessionSpec,
    AgentTurnRequest,
    MCPServerSpec,
)
from vibesys.agents.drivers import agentshim as subject
from vibesys.server.events import CommandResultPayload
from vs_sandbox import ProjectPathPolicy

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


@dataclass
class _Observer:
    events: list[AgentEvent] = field(default_factory=list)

    def on_event(self, event: AgentEvent) -> None:
        self.events.append(event)


class _GenerateOverride(Protocol):
    def __call__(
        self,
        prompt: str,
        /,
        *,
        cwd: str | None,
        timeout: int | None,
        silent: bool,
    ) -> str:
        """Replace one fake agent's generate behavior."""


class _FakeAgent:
    supports_native_output_schema = False
    native_output_schema_allows_arbitrary_keys = False
    native_output_schema_wants_absolute_path = False

    def __init__(
        self,
        model: str | None = None,
        event_handler: Any | None = None,  # noqa: ANN401  # tracked: #288
        *,
        executor: Any | None = None,  # noqa: ANN401  # tracked: #288
    ) -> None:
        self.model = model
        self.event_handler = event_handler
        self.executor = executor
        self.env = {"BASE": "one"}
        self.binary_path = "/bin/fake"
        self.sandbox = None
        self.session_id = "session-1"
        self.generate_calls: list[tuple[str, str | None, int | None]] = []
        self.install_calls: list[tuple[Path, list[Any]]] = []
        self.uninstall_calls: list[tuple[Path, list[Any]]] = []
        self.output_schema_paths: list[str | None] = []
        self.reasoning_effort: str | None = None
        self.error: BaseException | None = None
        self.generate_override: _GenerateOverride | None = None
        self.uninstall_override: Callable[[Path, list[Any]], None] | None = None
        self.tool_result_events: list[dict[str, Any]] = []
        self._last_session = SimpleNamespace(
            final_usage={"input_tokens": 12, "output_tokens": 3},
            total_cost_usd=0.25,
            duration_ms=90,
        )

    def set_reasoning_effort(self, effort: str) -> None:
        self.reasoning_effort = effort

    def set_output_schema_path(self, path: str | None) -> None:
        self.output_schema_paths.append(path)

    def install_mcp_servers(self, workspace: Path, servers: list[Any]) -> None:
        self.install_calls.append((workspace, servers))

    def uninstall_mcp_servers(self, workspace: Path, servers: list[Any]) -> None:
        if self.uninstall_override is not None:
            self.uninstall_override(workspace, servers)
            return
        self.uninstall_calls.append((workspace, servers))

    def generate(
        self,
        prompt: str,
        *,
        cwd: str | None,
        timeout: int | None,
        silent: bool,
    ) -> str:
        if self.generate_override is not None:
            return self.generate_override(prompt, cwd=cwd, timeout=timeout, silent=silent)
        assert silent
        assert self.event_handler is not None
        self.generate_calls.append((prompt, cwd, timeout))
        self.event_handler.on_thinking("working")
        for tool_result in self.tool_result_events:
            self.event_handler.on_tool_result(**tool_result)
        self.event_handler.on_usage({"input_tokens": 12, "output_tokens": 3})
        if self.error is not None:
            raise self.error
        return "done"


@pytest.fixture
def fake_agent(monkeypatch: pytest.MonkeyPatch) -> list[_FakeAgent]:
    built: list[_FakeAgent] = []

    def factory(
        model: str | None = None,
        event_handler: Any | None = None,  # noqa: ANN401  # tracked: #288
        *,
        executor: Any | None = None,  # noqa: ANN401  # tracked: #288
    ) -> _FakeAgent:
        agent = _FakeAgent(model, event_handler, executor=executor)
        built.append(agent)
        return agent

    monkeypatch.setitem(subject._PROVIDER_CLASSES, "codex", factory)  # noqa: SLF001
    monkeypatch.setattr(subject, "declare_agent_host_resources", lambda *_args, **_kwargs: ())
    monkeypatch.setattr(subject, "build_host_sandbox", lambda *_args, **_kwargs: "sandbox")
    return built


def _spec(tmp_path: Path, **changes: Any) -> AgentSessionSpec:  # noqa: ANN401
    values: dict[str, Any] = {
        "role": "implementer",
        "provider": "codex",
        "workspace": tmp_path,
        "model": "gpt-test",
        "policy": AgentExecutionPolicy(require_enforcement=True),
        "environment": (("GPU", "0"),),
        "reasoning_effort": "high",
    }
    values.update(changes)
    return AgentSessionSpec(**values)


def test_session_setup_applies_policy_model_environment_and_reasoning(
    fake_agent: list[_FakeAgent],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = ProjectPathPolicy(read_only_paths=("OBJECTIVE.md",))
    captured: dict[str, Any] = {}

    def build_sandbox(workspace: Path, **kwargs: Any) -> str:  # noqa: ANN401
        captured.update(workspace=workspace, **kwargs)
        return "confined"

    monkeypatch.setattr(subject, "build_host_sandbox", build_sandbox)
    driver = subject.AgentShimDriver(provider="codex")
    driver.create_session(
        _spec(
            tmp_path,
            policy=AgentExecutionPolicy(project_paths=policy, require_enforcement=True),
        )
    )

    agent = fake_agent[0]
    assert agent.model == "gpt-test"
    assert agent.env == {"BASE": "one", "GPU": "0"}
    assert agent.reasoning_effort == "high"
    assert agent.sandbox == "confined"
    assert captured["workspace"] == tmp_path
    assert captured["project_path_policy"] is policy
    assert captured["require_enforcement"] is True


def test_turn_forwards_prompt_timeout_events_and_usage(
    fake_agent: list[_FakeAgent], tmp_path: Path
) -> None:
    driver = subject.AgentShimDriver(provider="codex", timeout=30)
    session = driver.create_session(_spec(tmp_path))
    observer = _Observer()

    result = session.run_turn(
        AgentTurnRequest(
            message="Do it",
            instructions="Follow these rules",
            timeout=timedelta(seconds=7),
        ),
        observer,
    )

    assert result.text == "done"
    assert result.usage.input_tokens == 12
    assert result.usage.output_tokens == 3
    assert result.usage.total_cost_usd == 0.25
    assert result.provider_session_id == "session-1"
    assert fake_agent[0].generate_calls == [("Follow these rules\n\nDo it", str(tmp_path), 7)]
    assert [event.kind for event in observer.events] == [
        subject.AgentEventKind.THINKING,
        subject.AgentEventKind.USAGE,
    ]


def test_turn_translates_tool_results_into_typed_command_payloads(
    fake_agent: list[_FakeAgent], tmp_path: Path
) -> None:
    driver = subject.AgentShimDriver(provider="codex")
    session = driver.create_session(_spec(tmp_path))
    fake_agent[0].tool_result_events = [
        {"tool": "shell", "stdout": "out", "stderr": "warn", "exit_code": 3, "duration": 0.7}
    ]
    observer = _Observer()

    session.run_turn(AgentTurnRequest(message="Do it"), observer)

    event = next(
        event for event in observer.events if event.kind is subject.AgentEventKind.TOOL_RESULT
    )
    assert event.text == "out"
    assert event.payload["result_payload"] == CommandResultPayload(
        stdout="out",
        stderr="warn",
        exit_code=3,
        duration=0.7,
    )
    # The flat fields remain for consumers that predate the typed payload.
    assert event.payload["stdout"] == "out"
    assert event.payload["stderr"] == "warn"
    assert event.payload["exit_code"] == 3
    assert event.payload["duration"] == 0.7


def test_independent_sessions_overlap_and_chat_cleanup_does_not_interrupt_optimizer(
    fake_agent: list[_FakeAgent], tmp_path: Path
) -> None:
    barrier = threading.Barrier(2)
    release_optimizer = threading.Event()
    optimizer_observer = _Observer()
    chat_observer = _Observer()
    driver = subject.AgentShimDriver(provider="codex")
    optimizer = driver.create_session(_spec(tmp_path, role="implementer"))
    chat = driver.create_session(
        _spec(tmp_path, role="chat", environment=(("CHAT_MODE", "read-only"),))
    )

    def generate_optimizer(
        _prompt: str,
        *,
        cwd: str | None,
        timeout: int | None,
        silent: bool,
    ) -> str:
        assert cwd == str(tmp_path)
        assert timeout is None
        assert silent
        barrier.wait(timeout=2)
        assert fake_agent[0].event_handler is not None
        fake_agent[0].event_handler.on_thinking("optimizer event")
        assert release_optimizer.wait(timeout=2)
        return "optimizer result"

    def generate_chat(
        _prompt: str,
        *,
        cwd: str | None,
        timeout: int | None,
        silent: bool,
    ) -> str:
        assert cwd == str(tmp_path)
        assert timeout is None
        assert silent
        barrier.wait(timeout=2)
        assert fake_agent[1].event_handler is not None
        fake_agent[1].event_handler.on_thinking("chat event")
        return "chat result"

    fake_agent[0].generate_override = generate_optimizer
    fake_agent[1].generate_override = generate_chat

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        optimizer_turn = pool.submit(
            optimizer.run_turn,
            AgentTurnRequest(message="optimize"),
            optimizer_observer,
        )
        chat_turn = pool.submit(
            chat.run_turn,
            AgentTurnRequest(message="explain the run"),
            chat_observer,
        )
        assert chat_turn.result(timeout=3).text == "chat result"
        chat.close()
        assert not optimizer_turn.done()
        release_optimizer.set()
        assert optimizer_turn.result(timeout=3).text == "optimizer result"

    assert fake_agent[0].env == {"BASE": "one", "GPU": "0"}
    assert fake_agent[1].env == {"BASE": "one", "CHAT_MODE": "read-only"}
    assert [event.text for event in optimizer_observer.events] == ["optimizer event"]
    assert [event.text for event in chat_observer.events] == ["chat event"]
    driver.close()


def test_mcp_is_session_scoped_but_activated_only_around_turn(
    fake_agent: list[_FakeAgent], tmp_path: Path
) -> None:
    server = MCPServerSpec(name="issues", command="python", args=("-m", "issues"))
    session = subject.AgentShimDriver(provider="codex").create_session(
        _spec(tmp_path, mcp_servers=(server,))
    )

    session.run_turn(AgentTurnRequest(message="review"))

    installed = fake_agent[0].install_calls[0][1][0]
    assert installed.name == "issues"
    assert installed.command == subject.sys.executable
    assert installed.args == ["-m", "issues"]
    assert fake_agent[0].uninstall_calls == fake_agent[0].install_calls


def test_mcp_cleanup_preserves_original_turn_error(
    fake_agent: list[_FakeAgent], tmp_path: Path
) -> None:
    session = subject.AgentShimDriver(provider="codex").create_session(
        _spec(tmp_path, mcp_servers=(MCPServerSpec(name="issues", command="server"),))
    )
    fake_agent[0].error = ValueError("turn failed")

    def fail_cleanup(_workspace: Path, _servers: list[Any]) -> None:
        raise OSError("cleanup failed")  # noqa: TRY003  # tracked: #288

    fake_agent[0].uninstall_override = fail_cleanup

    with pytest.raises(ValueError, match="turn failed"):
        session.run_turn(AgentTurnRequest(message="review"))


def test_driver_and_session_close_are_idempotent(
    fake_agent: list[_FakeAgent], tmp_path: Path
) -> None:
    assert fake_agent == []
    driver = subject.AgentShimDriver(provider="codex")
    session = driver.create_session(_spec(tmp_path))

    session.close()
    session.close()
    driver.close()
    driver.close()

    with pytest.raises(RuntimeError, match="closed"):
        session.run_turn(AgentTurnRequest(message="later"))
    with pytest.raises(RuntimeError, match="closed"):
        driver.create_session(_spec(tmp_path))
