"""Tail subscriptions, bounded event queries, and the snapshot projections.

These are the server half of booting a client without replaying the whole run:
a subscription that delivers only a suffix, the run-level events that suffix
would otherwise lose, and the backfill query that walks history backwards.
"""

import json
import socket
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest

from vibesys.server.events import (
    ChatData,
    ChatThreadCreatedData,
    EventType,
    OutputData,
    RoundFinishedData,
    RunEvent,
    RunStartedData,
)
from vibesys.server.protocol import EventsQuery, SnapshotQuery, SubscribeRequest
from vibesys.server.service import SupervisionService
from vibesys.server.supervisor import RunSupervisor
from vibesys.server.transport import SupervisionSocketServer

_TIMESTAMP = datetime(2026, 1, 1, tzinfo=UTC)
_ROUND_EVERY = 25


def _event(sequence: int, event_type: EventType, **fields: object) -> RunEvent:
    return RunEvent(
        sequence=sequence, run_id="persisted-run", timestamp=_TIMESTAMP, type=event_type, **fields
    )


def _round_log(count: int, *, with_threads: bool = False) -> list[RunEvent]:
    """A run log with a run_started header and a round_finished every 25 events."""
    events = [
        _event(
            1,
            EventType.RUN_STARTED,
            data=RunStartedData(outer_loop="agent", input="objective", max_rounds=24),
        )
    ]
    thread_id = "thread-1"
    if with_threads:
        events.append(
            _event(
                2,
                EventType.CHAT_THREAD_CREATED,
                chat_thread_id=thread_id,
                data=ChatThreadCreatedData(
                    thread_id=thread_id,
                    driver="agentshim",
                    provider="claude",
                    model="opus",
                    created_at=_TIMESTAMP,
                ),
            )
        )
    for sequence in range(len(events) + 1, count + 1):
        if sequence % _ROUND_EVERY == 0:
            events.append(
                _event(
                    sequence,
                    EventType.ROUND_FINISHED,
                    round_label=f"round-{sequence // _ROUND_EVERY}",
                    data=RoundFinishedData(attempts=1, judge_verdict="pass"),
                )
            )
        elif with_threads and sequence == count - 1:
            # A late turn backfills the thread title, which is exactly why the
            # registry cannot be rebuilt from a tail alone.
            events.append(
                _event(
                    sequence,
                    EventType.CHAT,
                    text="why did round two regress?",
                    chat_thread_id=thread_id,
                    data=ChatData(answer="because", thread_title="why did round two regress?"),
                )
            )
        else:
            events.append(
                _event(
                    sequence,
                    EventType.OUTPUT,
                    text=f"line-{sequence}",
                    data=OutputData(stream="stdout", content=f"line-{sequence}"),
                )
            )
    return events


def _attach(tmp_path: Path, events: list[RunEvent]) -> RunSupervisor:
    log_dir = tmp_path / "logs"
    log_dir.mkdir(exist_ok=True)
    (log_dir / "run-events.jsonl").write_text(
        "".join(event.model_dump_json() + "\n" for event in events)
    )
    supervisor = RunSupervisor()
    supervisor.attach(log_dir)
    return supervisor


def _subscribe(service: SupervisionService, request: SubscribeRequest) -> tuple[dict, dict]:
    """Run one real subscription over the socket and return its first two messages."""
    socket_path = Path("/tmp") / f"vibesys-test-{uuid.uuid4().hex}.sock"  # noqa: S108
    with SupervisionSocketServer(socket_path, service):  # noqa: SIM117
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(5)
            client.connect(str(socket_path))
            stream = client.makefile("rwb")
            stream.write(request.model_dump_json().encode() + b"\n")
            stream.flush()
            return json.loads(stream.readline()), json.loads(stream.readline())


def test_subscribe_without_tail_delivers_everything_and_reports_no_floor(tmp_path):  # noqa: ANN001, ANN201
    supervisor = _attach(tmp_path, _round_log(200))
    service = SupervisionService(supervisor)

    subscribed, batch = _subscribe(service, SubscribeRequest(after_sequence=0))

    assert subscribed["type"] == "subscribed"
    assert batch["history_after_sequence"] == 0
    assert [event["sequence"] for event in batch["events"]] == list(
        range(1, batch["through_sequence"] + 1)
    )


def test_subscribe_with_tail_delivers_the_tail_and_reports_its_floor(tmp_path):  # noqa: ANN001, ANN201
    supervisor = _attach(tmp_path, _round_log(200))
    service = SupervisionService(supervisor)
    latest = supervisor.snapshot().sequence

    _subscribed, batch = _subscribe(service, SubscribeRequest(after_sequence=0, tail=40))

    floor = latest - 40
    assert batch["history_after_sequence"] == floor
    assert batch["through_sequence"] == latest
    tail = [event for event in batch["events"] if event["sequence"] > floor]
    assert [event["sequence"] for event in tail] == list(range(floor + 1, latest + 1))


def test_a_tail_subscription_replays_the_pre_floor_run_level_events(tmp_path):  # noqa: ANN001, ANN201
    supervisor = _attach(tmp_path, _round_log(200))
    service = SupervisionService(supervisor)
    latest = supervisor.snapshot().sequence
    floor = latest - 40

    _subscribed, batch = _subscribe(service, SubscribeRequest(after_sequence=0, tail=40))

    sequences = [event["sequence"] for event in batch["events"]]
    assert sequences == sorted(sequences), "clients drop out-of-order events"
    pre_floor = [event for event in batch["events"] if event["sequence"] <= floor]
    assert [event["type"] for event in pre_floor] == ["run_started"] + [
        "round_finished" for _ in range(floor // _ROUND_EVERY)
    ]


def test_a_tail_subscription_without_pre_floor_run_level_events_delivers_only_the_tail(tmp_path):  # noqa: ANN001, ANN201
    events = [
        _event(
            sequence,
            EventType.OUTPUT,
            text=f"line-{sequence}",
            data=OutputData(stream="stdout", content=f"line-{sequence}"),
        )
        for sequence in range(1, 121)
    ]
    supervisor = _attach(tmp_path, events)
    service = SupervisionService(supervisor)
    latest = supervisor.snapshot().sequence
    floor = latest - 20

    _subscribed, batch = _subscribe(service, SubscribeRequest(after_sequence=0, tail=20))

    assert [event["sequence"] for event in batch["events"]] == list(range(floor + 1, latest + 1))


def test_the_spine_does_not_move_the_watermarks(tmp_path):  # noqa: ANN001, ANN201
    supervisor = _attach(tmp_path, _round_log(200))
    service = SupervisionService(supervisor)
    latest = supervisor.snapshot().sequence

    _subscribed, with_spine = _subscribe(service, SubscribeRequest(after_sequence=0, tail=40))
    _subscribed, no_tail = _subscribe(service, SubscribeRequest(after_sequence=0))

    assert with_spine["through_sequence"] == no_tail["through_sequence"] == latest
    assert with_spine["history_after_sequence"] == latest - 40
    assert no_tail["history_after_sequence"] == 0


def test_a_tail_subscription_parses_only_the_tail_and_the_spine(tmp_path):  # noqa: ANN001, ANN201
    count = 12_000
    supervisor = _attach(tmp_path, _round_log(count))
    store = supervisor._store  # noqa: SLF001  # accounting is the assertion
    assert store is not None
    parsed_at_attach = store.parsed_record_count

    through, events, _active = supervisor.subscription_checkpoint(count - 500, bootstrap_spine=True)

    spine_records = 1 + (count - 500) // _ROUND_EVERY
    assert store.parsed_record_count <= parsed_at_attach + 500 + spine_records
    assert store.parsed_record_count < count
    assert through >= count
    assert len(events) < count


def test_events_query_returns_the_half_open_range(tmp_path):  # noqa: ANN001, ANN201
    supervisor = _attach(tmp_path, _round_log(200))
    service = SupervisionService(supervisor)

    response = service.execute(EventsQuery(after_sequence=50, before_sequence=60))

    assert [event.sequence for event in response.events] == list(range(51, 60))


def test_events_query_without_an_upper_bound_is_unchanged(tmp_path):  # noqa: ANN001, ANN201
    supervisor = _attach(tmp_path, _round_log(200))
    service = SupervisionService(supervisor)

    bounded = service.execute(EventsQuery(after_sequence=50, before_sequence=None))
    open_ended = service.execute(EventsQuery(after_sequence=50))

    assert [event.sequence for event in bounded.events] == [
        event.sequence for event in open_ended.events
    ]
    assert open_ended.events[-1].sequence == supervisor.snapshot().sequence


def test_backfill_walks_history_backwards_without_gaps(tmp_path):  # noqa: ANN001, ANN201
    """The client's backfill loop: lower the floor a chunk at a time."""
    supervisor = _attach(tmp_path, _round_log(200))
    service = SupervisionService(supervisor)
    floor = 150
    collected: list[int] = []

    while floor > 0:
        after = max(0, floor - 40)
        response = service.execute(EventsQuery(after_sequence=after, before_sequence=floor + 1))
        collected = [event.sequence for event in response.events] + collected
        floor = after

    assert collected == list(range(1, 151))


@pytest.mark.parametrize("before_sequence", [0, -1])
def test_events_query_rejects_a_meaningless_upper_bound(before_sequence):  # noqa: ANN001, ANN201
    with pytest.raises(ValueError):  # noqa: PT011
        EventsQuery(after_sequence=0, before_sequence=before_sequence)


def test_snapshot_carries_the_chat_thread_registry_after_attach(tmp_path):  # noqa: ANN001, ANN201
    supervisor = _attach(tmp_path, _round_log(200, with_threads=True))
    service = SupervisionService(supervisor)

    response = service.execute(SnapshotQuery())

    assert response.snapshot is not None
    assert [(thread.thread_id, thread.title) for thread in response.snapshot.chat_threads] == [
        ("thread-1", "why did round two regress?")
    ]
    assert response.snapshot.chat_threads[0].provider == "claude"


def test_snapshot_chat_threads_default_to_empty(tmp_path):  # noqa: ANN001, ANN201
    supervisor = _attach(tmp_path, _round_log(20))

    assert supervisor.snapshot().chat_threads == []
