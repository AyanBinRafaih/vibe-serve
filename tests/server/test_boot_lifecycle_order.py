"""Boot the server stack in the order a real launch uses, and check the cost.

Every other subscription test attaches the run's durable event store and
*then* subscribes. A real boot does the opposite: the socket server, the
supervisor, and the service come up on a bootstrap directory, the client
connects and subscribes immediately, and only later does run assembly call
``RunSupervisor.attach`` with the run's own log directory (see
``vibesys/context.py``). Everything a tail subscription is supposed to bound
is decided at subscribe time, so the two orders are not interchangeable, and
only this one reproduces a client folding a whole run's history at boot.

The run history here is produced by the mock agent driver rather than
handwritten events, so it travels the real client -> sink -> supervisor path
and has the event mix a run actually writes.
"""

from __future__ import annotations

import json
import socket
import uuid
from pathlib import Path
from typing import Any

import pytest

from vibesys.agents.client import AgentClient
from vibesys.agents.drivers.mock import MockDriver, ScriptedPlaybook
from vibesys.schemas import OrchestratorPlan
from vibesys.server.events import (
    EventType,
    RoundFinishedData,
    RunStartedData,
)
from vibesys.server.protocol import SubscribeRequest
from vibesys.server.registry import REGISTRY
from vibesys.server.service import SupervisionService
from vibesys.server.supervisor import RunSupervisor
from vibesys.server.transport import SupervisionSocketServer

TAIL = 1000
"""What the TUI client asks for, and therefore what the server must bound to."""

_TARGET_EVENTS = 3500
_ROUNDS_PER_SPINE_EVENT = 1

# One RUN_STARTED, one ROUND_FINISHED per recorded round, one
# EXPERIMENTS_CHANGED after attach. The test writes these itself so the
# expected bound is arithmetic the test owns rather than a server internal.
_RUN_STARTED_EVENTS = 1
_POST_ATTACH_SPINE_EVENTS = 1


def _socket_path() -> Path:
    """A short-enough Unix socket path; ``AF_UNIX`` caps it around 100 bytes."""
    return Path("/tmp") / f"vibesys-boot-{uuid.uuid4().hex[:12]}.sock"  # noqa: S108


class _Client:
    """A socket client that reads whole protocol messages, one at a time."""

    def __init__(self, socket_path: Path) -> None:
        self._socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._socket.settimeout(30)
        self._socket.connect(str(socket_path))
        self._stream = self._socket.makefile("rwb")

    def send(self, request: SubscribeRequest) -> None:
        self._stream.write(request.model_dump_json().encode() + b"\n")
        self._stream.flush()

    def read_message(self) -> dict[str, Any]:
        line = self._stream.readline()
        assert line, "server closed the stream"
        return json.loads(line)

    def read_batch(self) -> dict[str, Any]:
        """Read forward to the next event batch, skipping the subscribe ack."""
        while True:
            message = self.read_message()
            if "through_sequence" in message:
                return message

    def close(self) -> None:
        self._stream.close()
        self._socket.close()


@pytest.fixture
def recorded_run(tmp_path) -> tuple[Path, int]:  # noqa: ANN001
    """Write a run's durable event log with the scripted mock driver.

    Returns the log directory and how many spine (run-level) events it holds.
    """
    log_dir = tmp_path / "run-logs"
    producer = RunSupervisor()
    producer.attach(log_dir)
    REGISTRY.activate(producer)
    rounds = 0
    try:
        producer.record(
            EventType.RUN_STARTED,
            data=RunStartedData(outer_loop="agent", input="objective", max_rounds=999),
        )
        driver = MockDriver(
            ScriptedPlaybook(
                text_chunks=6,
                thinking_chunks=2,
                tool_calls=4,
                tool_result_chars=512,
                todo_updates=1,
            )
        )
        client = AgentClient(driver, driver_name="mock", provider="mock", model_name="mock-model")
        while producer.snapshot().sequence < _TARGET_EVENTS:
            rounds += 1
            label = f"round {rounds}"
            producer.record(
                EventType.ROUND_FINISHED,
                round_label=label,
                data=RoundFinishedData(attempts=1, judge_verdict="pass"),
            )
            client.invoke(
                kind="orchestrator",
                workspace=tmp_path,
                system_prompt="plan the round",
                user_prompt="what next?",
                response_cls=OrchestratorPlan,
                fallback_factory=lambda: OrchestratorPlan(
                    hypothesis_id="",
                    hypothesis="",
                    task="",
                    pass_criteria="",
                    reasoning="fallback",
                ),
                round_label=label,
            )
        client.close()
    finally:
        REGISTRY.deactivate(producer)
    return log_dir, _RUN_STARTED_EVENTS + rounds * _ROUNDS_PER_SPINE_EVENT


@pytest.fixture
def booted(tmp_path):  # noqa: ANN001, ANN201
    """Bring up supervisor, service, and socket server the way a launch does."""
    supervisor = RunSupervisor()
    supervisor.attach(tmp_path / "bootstrap")
    service = SupervisionService(supervisor)
    socket_path = _socket_path()
    server = SupervisionSocketServer(socket_path, service)
    server.start()
    try:
        yield supervisor, socket_path
    finally:
        server.close()


def test_the_mock_driver_writes_a_run_sized_event_history(recorded_run):  # noqa: ANN001, ANN201
    """The fixture is only meaningful if it really produced a large history."""
    log_dir, _spine = recorded_run
    lines = (log_dir / "run-events.jsonl").read_text().splitlines()

    assert len(lines) >= _TARGET_EVENTS


def test_a_store_attached_after_subscribe_still_delivers_only_a_tail(recorded_run, booted):  # noqa: ANN001, ANN201
    """The real boot order: subscribe first, attach the run's store second.

    The tail floor is computed once, against whatever store was attached when
    the subscription opened. If it is not recomputed when a much larger store
    replaces it, the first live checkpoint hands the client the entire run.
    """
    log_dir, spine_events = recorded_run
    supervisor, socket_path = booted
    client = _Client(socket_path)
    try:
        client.send(SubscribeRequest(after_sequence=0, tail=TAIL))
        bootstrap_batch = client.read_batch()
        assert bootstrap_batch["through_sequence"] < TAIL, "the pre-attach store must be small"

        supervisor.attach(log_dir)
        supervisor.record(EventType.EXPERIMENTS_CHANGED, "experiments")

        batch = client.read_batch()
    finally:
        client.close()

    latest = supervisor.snapshot().sequence
    allowed = TAIL + spine_events + _POST_ATTACH_SPINE_EVENTS
    assert latest > _TARGET_EVENTS, "attach must have swapped in the large store"
    assert len(batch["events"]) <= allowed, (
        f"a tail subscription received {len(batch['events'])} events of a "
        f"{latest}-event history; the tail plus its spine is at most {allowed}"
    )
    assert batch["history_after_sequence"] >= latest - TAIL, (
        "the reported floor must move with the store that replaced the "
        "bootstrap one, or the client backfills history it was never sent"
    )


def test_a_late_attach_still_replays_the_run_level_spine(recorded_run, booted):  # noqa: ANN001, ANN201
    """A bounded batch is only correct if it still carries run-level state.

    Bounding the delivery is not allowed to cost the client the events its
    reducer needs to know a run started and which rounds finished.
    """
    log_dir, _spine = recorded_run
    supervisor, socket_path = booted
    client = _Client(socket_path)
    try:
        client.send(SubscribeRequest(after_sequence=0, tail=TAIL))
        client.read_batch()

        supervisor.attach(log_dir)
        supervisor.record(EventType.EXPERIMENTS_CHANGED, "experiments")

        batch = client.read_batch()
    finally:
        client.close()

    floor = batch["history_after_sequence"]
    pre_floor_types = [event["type"] for event in batch["events"] if event["sequence"] <= floor]
    sequences = [event["sequence"] for event in batch["events"]]

    assert sequences == sorted(sequences), "clients drop out-of-order events"
    assert EventType.RUN_STARTED.value in pre_floor_types
    assert EventType.ROUND_FINISHED.value in pre_floor_types


def test_the_same_history_attached_before_subscribe_is_already_bounded(recorded_run):  # noqa: ANN001, ANN201
    """The order the existing benches use, kept as the contrast case.

    This passes today. Its only job is to show that the order, not the size
    of the history, is what the regression above is about.
    """
    log_dir, spine_events = recorded_run
    supervisor = RunSupervisor()
    supervisor.attach(log_dir)
    service = SupervisionService(supervisor)
    socket_path = _socket_path()
    server = SupervisionSocketServer(socket_path, service)
    server.start()
    client = _Client(socket_path)
    try:
        client.send(SubscribeRequest(after_sequence=0, tail=TAIL))
        batch = client.read_batch()
    finally:
        client.close()
        server.close()

    latest = supervisor.snapshot().sequence
    assert len(batch["events"]) <= TAIL + spine_events
    assert batch["history_after_sequence"] == latest - TAIL
