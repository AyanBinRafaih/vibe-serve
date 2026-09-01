"""Composition and terminal-event tests for the interactive server runtime."""

import json
import socket
import threading
import time
from pathlib import Path

import pytest

from server.api.protocol import SubscribeRequest
from server.api.service import RunApi
from server.chat.manager import ChatManager
from server.controller import RunController
from server.execution import ExecutionTracker
from server.integration import RunIntegrationAdapter
from server.journal import EventJournal
from server.runtime import ServerRuntime
from vibesys.errors import ConfigurationDiagnostic, ConfigurationError
from vibesys.run.events import CoreEventType, EventStatus


def _collect_until(socket_path: Path, terminal_type: str, received: list[dict]) -> None:
    deadline = time.monotonic() + 5
    while not socket_path.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(5)
        client.connect(str(socket_path))
        stream = client.makefile("rwb")
        stream.write(SubscribeRequest(after_sequence=0).model_dump_json().encode() + b"\n")
        stream.flush()
        while True:
            message = json.loads(stream.readline())
            events = message.get("events", [])
            received.extend(events)
            if any(event["type"] == terminal_type for event in events):
                return


def test_runtime_explicitly_composes_server_components(tmp_path):  # noqa: ANN001, ANN201
    runtime = ServerRuntime(socket_path=tmp_path / "control.sock")

    assert isinstance(runtime.journal, EventJournal)
    assert isinstance(runtime.executions, ExecutionTracker)
    assert isinstance(runtime.controller, RunController)
    assert isinstance(runtime.chat, ChatManager)
    assert isinstance(runtime.integration, RunIntegrationAdapter)
    assert isinstance(runtime.api, RunApi)
    assert runtime.chat.terminal_retention_enabled()

    runtime.integration.close()


def test_runtime_streams_success_before_client_disconnect(tmp_path):  # noqa: ANN001, ANN201
    socket_path = tmp_path / "control.sock"
    runtime = ServerRuntime(socket_path=socket_path)
    received: list[dict] = []
    subscriber = threading.Thread(
        target=_collect_until,
        args=(socket_path, "run_finished", received),
    )
    subscriber.start()

    value = runtime.run(lambda: "ran")

    subscriber.join(timeout=5)
    assert value == "ran"
    assert not subscriber.is_alive()
    assert any(event["type"] == "server_ready" for event in received)
    assert sum(event["type"] == "run_finished" for event in received) == 1
    assert not socket_path.exists()


def test_runtime_does_not_duplicate_core_terminal_event(tmp_path):  # noqa: ANN001, ANN201
    socket_path = tmp_path / "control.sock"
    runtime = ServerRuntime(socket_path=socket_path)
    received: list[dict] = []
    subscriber = threading.Thread(
        target=_collect_until,
        args=(socket_path, "run_finished", received),
    )
    subscriber.start()

    def run() -> None:
        runtime.integration.events.emit(
            CoreEventType.RUN_FINISHED,
            status=EventStatus.COMPLETED,
        )

    runtime.run(run)

    subscriber.join(timeout=5)
    assert not subscriber.is_alive()
    assert sum(event["type"] == "run_finished" for event in received) == 1
    assert sum(event.type.value == "run_finished" for event in runtime.journal.read()) == 1


def test_runtime_streams_configuration_failure_without_run_failure(tmp_path):  # noqa: ANN001, ANN201
    socket_path = tmp_path / "control.sock"
    runtime = ServerRuntime(socket_path=socket_path)
    received: list[dict] = []
    subscriber = threading.Thread(
        target=_collect_until,
        args=(socket_path, "configuration_failed", received),
    )
    subscriber.start()
    failure = ConfigurationError(
        ConfigurationDiagnostic(
            code="invalid_arguments",
            stage="argument_parsing",
            message="unknown token=super-secret option --bad",
            usage="usage: vibesys --token=super-secret",
        )
    )

    with pytest.raises(ConfigurationError) as raised:
        runtime.run(lambda: (_ for _ in ()).throw(failure))

    assert raised.value is failure
    subscriber.join(timeout=5)
    assert not subscriber.is_alive()
    event = next(event for event in received if event["type"] == "configuration_failed")
    assert event["data"]["code"] == "invalid_arguments"
    assert event["data"]["message"] == "unknown token=[REDACTED] option --bad"
    assert event["data"]["usage"] == "usage: vibesys --token=[REDACTED]"
    assert not any(event["type"] == "run_failed" for event in received)
