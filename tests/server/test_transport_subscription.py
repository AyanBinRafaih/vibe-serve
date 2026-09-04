"""Tail subscription, bounded backfill, and late-attach transport contracts."""

import json
import socket
import threading
import uuid
from collections.abc import Callable, Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import TypedDict, Unpack

import pytest
from tests.server.support import ServerParts, build_server_parts

from server.api.protocol import EventsQuery, SnapshotQuery, SubscribeRequest
from server.api.service import RunApi
from server.events import (
    ChatData,
    ChatThreadCreatedData,
    EventData,
    EventStore,
    EventType,
    OutputData,
    RoundFinishedData,
    RunEvent,
    RunStartedData,
)
from server.journal import _BOOTSTRAP_SPINE_TYPES
from server.transport.unix_jsonl import UnixJsonlServer

_TIMESTAMP = datetime(2026, 1, 1, tzinfo=UTC)
_ROUND_EVERY = 25


class _EventFields(TypedDict, total=False):
    """Run-event fields varied by the fixture builder."""

    data: EventData
    chat_thread_id: str
    round_label: str
    text: str


def _event(sequence: int, event_type: EventType, **fields: Unpack[_EventFields]) -> RunEvent:
    return RunEvent(
        sequence=sequence,
        run_id="persisted-run",
        timestamp=_TIMESTAMP,
        type=event_type,
        **fields,
    )


def _round_log(count: int, *, with_threads: bool = False) -> list[RunEvent]:
    events = [
        _event(
            1,
            EventType.RUN_STARTED,
            data=RunStartedData(outer_loop="agent", input="objective", max_rounds=24),
        )
    ]
    if with_threads:
        events.append(
            _event(
                2,
                EventType.CHAT_THREAD_CREATED,
                chat_thread_id="thread-1",
                data=ChatThreadCreatedData(
                    thread_id="thread-1",
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
            events.append(
                _event(
                    sequence,
                    EventType.CHAT,
                    text="why did round two regress?",
                    chat_thread_id="thread-1",
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


def _spine_records(floor: int) -> int:
    return 1 + floor // _ROUND_EVERY


def _write_log(log_dir: Path, events: list[RunEvent]) -> Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "run-events.jsonl").write_text(
        "".join(event.model_dump_json() + "\n" for event in events)
    )
    return log_dir


def _attach(tmp_path: Path, events: list[RunEvent]) -> ServerParts:
    return build_server_parts(_write_log(tmp_path / "logs", events))


@contextmanager
def _live_subscription(api: RunApi, request: SubscribeRequest) -> Generator[Callable[[], dict]]:
    socket_path = Path("/tmp") / f"vibesys-test-{uuid.uuid4().hex}.sock"  # noqa: S108
    with UnixJsonlServer(socket_path, api):  # noqa: SIM117
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(10)
            client.connect(str(socket_path))
            stream = client.makefile("rwb")
            stream.write(request.model_dump_json().encode() + b"\n")
            stream.flush()
            yield lambda: json.loads(stream.readline())


def _subscribe(api: RunApi, request: SubscribeRequest) -> tuple[dict, dict]:
    with _live_subscription(api, request) as read:
        return read(), read()


@pytest.mark.parametrize("tail", [None, 40])
def test_subscription_replays_requested_history(tmp_path, tail):  # noqa: ANN001, ANN201
    parts = _attach(tmp_path, _round_log(200))
    latest = parts.api.snapshot().sequence

    subscribed, batch = _subscribe(parts.api, SubscribeRequest(after_sequence=0, tail=tail))

    assert subscribed["type"] == "subscribed"
    floor = 0 if tail is None else latest - tail
    assert batch["history_after_sequence"] == floor
    assert batch["through_sequence"] == latest
    replayed_tail = [event["sequence"] for event in batch["events"] if event["sequence"] > floor]
    assert replayed_tail == list(range(floor + 1, latest + 1))


def test_tail_replays_pre_floor_run_spine_in_order(tmp_path):  # noqa: ANN001, ANN201
    parts = _attach(tmp_path, _round_log(200))
    latest = parts.api.snapshot().sequence
    floor = latest - 40

    _subscribed, batch = _subscribe(parts.api, SubscribeRequest(after_sequence=0, tail=40))

    sequences = [event["sequence"] for event in batch["events"]]
    assert sequences == sorted(sequences)
    pre_floor = [event for event in batch["events"] if event["sequence"] <= floor]
    assert [event["type"] for event in pre_floor] == ["run_started"] + [
        "round_finished" for _ in range(floor // _ROUND_EVERY)
    ]


def test_tail_without_spine_events_delivers_only_suffix(tmp_path):  # noqa: ANN001, ANN201
    events = [
        _event(
            sequence,
            EventType.OUTPUT,
            data=OutputData(stream="stdout", content=f"line-{sequence}"),
        )
        for sequence in range(1, 121)
    ]
    parts = _attach(tmp_path, events)
    latest = parts.api.snapshot().sequence

    _subscribed, batch = _subscribe(parts.api, SubscribeRequest(after_sequence=0, tail=20))

    assert [event["sequence"] for event in batch["events"]] == list(range(latest - 19, latest + 1))


def _spine_type_values() -> set[str]:
    return {event_type.value for event_type in _BOOTSTRAP_SPINE_TYPES}


def test_bootstrap_tail_stays_bounded_when_events_land_mid_bootstrap(  # noqa: ANN201
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    tail = 40
    parts = _attach(tmp_path, _round_log(200))
    original_bootstrap = parts.api.subscription_bootstrap
    pre_burst_watermarks: list[int] = []

    def bursty_bootstrap(after_sequence: int, tail_bound: int | None):  # noqa: ANN202
        # Reproduce the bootstrap race deterministically: a burst of appends
        # lands after the handler commits to bootstrapping but before the
        # bootstrap's own locked read.
        pre_burst_watermarks.append(parts.api.latest_sequence)
        for index in range(3000):
            parts.journal.publish_output("stdout", f"burst-{index}")
        return original_bootstrap(after_sequence, tail_bound)

    monkeypatch.setattr(parts.api, "subscription_bootstrap", bursty_bootstrap)

    subscribed, batch = _subscribe(parts.api, SubscribeRequest(after_sequence=0, tail=tail))

    floor = batch["history_after_sequence"]
    through = batch["through_sequence"]
    ordinary = [event for event in batch["events"] if event["sequence"] > floor]
    context = (
        f"pre-burst watermarks {pre_burst_watermarks or None}, checkpoint watermark {through}, "
        f"requested tail {tail}, ordinary bootstrap events {len(ordinary)}"
    )
    assert len(pre_burst_watermarks) == 1, context
    assert through == pre_burst_watermarks[0] + 3000, context
    assert len(ordinary) <= tail, context
    assert through == parts.api.latest_sequence, context
    assert floor == through - tail, context
    assert subscribed["latest_sequence"] == through, context
    pre_floor = [event for event in batch["events"] if event["sequence"] <= floor]
    assert all(event["type"] in _spine_type_values() for event in pre_floor), context
    assert all(event["sequence"] <= through for event in batch["events"]), context


def test_bootstrap_failure_surfaces_after_the_handshake(  # noqa: ANN201
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    parts = _attach(tmp_path, _round_log(50))
    latest = parts.api.latest_sequence

    def failing_bootstrap(_after_sequence: int, _tail: int | None):  # noqa: ANN202
        raise RuntimeError

    monkeypatch.setattr(parts.api, "subscription_bootstrap", failing_bootstrap)

    with _live_subscription(parts.api, SubscribeRequest(after_sequence=0, tail=40)) as read:
        subscribed = read()
        failure = read()

    # The dial itself must succeed: the client probes tail support by dialing
    # and treats a pre-handshake failure as a server without the field, so a
    # transient replay fault would otherwise trigger a full-history retry.
    assert subscribed["type"] == "subscribed"
    assert subscribed["latest_sequence"] == latest
    assert failure["type"] == "protocol_error"
    assert failure["code"] == "stream_failed"


def test_subscription_bootstrap_captures_one_atomic_state(tmp_path):  # noqa: ANN001, ANN201
    parts = _attach(tmp_path, _round_log(200))
    latest = parts.api.latest_sequence

    bootstrap = parts.api.subscription_bootstrap(0, 40)

    assert bootstrap.run_id == parts.api.snapshot().run_id
    assert bootstrap.through_sequence == latest
    assert bootstrap.floor == latest - 40
    ordinary = [event.sequence for event in bootstrap.events if event.sequence > bootstrap.floor]
    assert ordinary == list(range(bootstrap.floor + 1, bootstrap.through_sequence + 1))
    pre_floor = [event for event in bootstrap.events if event.sequence <= bootstrap.floor]
    assert [event.type for event in pre_floor] == [EventType.RUN_STARTED] + [
        EventType.ROUND_FINISHED
    ] * (bootstrap.floor // _ROUND_EVERY)
    assert bootstrap.active_executions == []

    full = parts.api.subscription_bootstrap(0, None)
    assert full.floor == 0
    assert full.through_sequence == latest
    assert [event.sequence for event in full.events] == list(range(1, latest + 1))


def test_bootstrap_tail_stays_bounded_under_concurrent_appends(tmp_path):  # noqa: ANN001, ANN201
    tail = 10
    parts = _attach(tmp_path, _round_log(200))
    stop = threading.Event()

    def append_live_output() -> None:
        index = 0
        while not stop.is_set() and index < 20_000:
            parts.journal.publish_output("stdout", f"live-{index}")
            index += 1

    writer = threading.Thread(target=append_live_output)
    writer.start()
    try:
        _subscribed, batch = _subscribe(parts.api, SubscribeRequest(after_sequence=0, tail=tail))
    finally:
        stop.set()
        writer.join(timeout=5)
    assert not writer.is_alive()

    floor = batch["history_after_sequence"]
    ordinary = [event for event in batch["events"] if event["sequence"] > floor]
    assert len(ordinary) <= tail
    pre_floor = [event for event in batch["events"] if event["sequence"] <= floor]
    assert all(event["type"] in _spine_type_values() for event in pre_floor)
    assert all(event["sequence"] <= batch["through_sequence"] for event in batch["events"])


def test_checkpoint_parses_only_tail_and_spine(tmp_path):  # noqa: ANN001, ANN201
    count = 12_000
    parts = _attach(tmp_path, _round_log(count))
    store = parts.journal._store  # noqa: SLF001
    assert store is not None
    parsed_at_attach = store.parsed_record_count

    through, events, _active = parts.api.subscription_checkpoint(count - 500, bootstrap_spine=True)

    assert store.parsed_record_count <= (parsed_at_attach + 500 + _spine_records(count - 500))
    assert store.parsed_record_count < count
    assert through >= count
    assert len(events) < count


def test_events_query_is_half_open_and_backfills_without_gaps(tmp_path):  # noqa: ANN001, ANN201
    parts = _attach(tmp_path, _round_log(200))
    response = parts.api.execute(EventsQuery(after_sequence=50, before_sequence=60))
    assert [event.sequence for event in response.events] == list(range(51, 60))

    floor = 150
    collected: list[int] = []
    while floor > 0:
        after = max(0, floor - 40)
        response = parts.api.execute(EventsQuery(after_sequence=after, before_sequence=floor + 1))
        collected = [event.sequence for event in response.events] + collected
        floor = after
    assert collected == list(range(1, 151))


@pytest.mark.parametrize("before_sequence", [0, -1])
def test_events_query_rejects_meaningless_upper_bound(before_sequence):  # noqa: ANN001, ANN201
    with pytest.raises(ValueError):  # noqa: PT011
        EventsQuery(after_sequence=0, before_sequence=before_sequence)


def test_snapshot_reconstructs_chat_threads_from_full_history(tmp_path):  # noqa: ANN001, ANN201
    parts = _attach(tmp_path, _round_log(200, with_threads=True))

    response = parts.api.execute(SnapshotQuery())

    assert response.snapshot is not None
    assert [(thread.thread_id, thread.title) for thread in response.snapshot.chat_threads] == [
        ("thread-1", "why did round two regress?")
    ]
    assert response.snapshot.chat_threads[0].provider == "claude"


def test_late_attach_rebootstraps_at_fresh_tail_with_spine(tmp_path):  # noqa: ANN001, ANN201
    parts = build_server_parts(tmp_path / "server")

    with _live_subscription(parts.api, SubscribeRequest(after_sequence=0, tail=40)) as read:
        assert read()["type"] == "subscribed"
        bootstrap = read()
        parts.attach(_write_log(tmp_path / "logs", _round_log(200)))
        batch = read()

    assert bootstrap["history_after_sequence"] == 0
    latest = parts.api.snapshot().sequence
    floor = latest - 40
    assert batch["history_after_sequence"] == floor
    assert batch["through_sequence"] == latest
    pre_floor = [event for event in batch["events"] if event["sequence"] <= floor]
    assert [event["type"] for event in pre_floor] == ["run_started"] + [
        "round_finished" for _ in range(floor // _ROUND_EVERY)
    ]
    assert len(batch["events"]) <= 40 + _spine_records(floor)


def test_late_attach_does_not_parse_skipped_history(tmp_path):  # noqa: ANN001, ANN201
    count = 8_000
    log_dir = _write_log(tmp_path / "logs", _round_log(count))
    parts = build_server_parts(tmp_path / "server")

    with _live_subscription(parts.api, SubscribeRequest(after_sequence=0, tail=40)) as read:
        read()
        read()
        parts.attach(log_dir)
        batch = read()

    store = parts.journal._store  # noqa: SLF001
    assert store is not None
    floor = batch["history_after_sequence"]
    attach_only = EventStore(log_dir / "run-events.jsonl", run_id="persisted-run")
    assert store.parsed_record_count <= (
        attach_only.parsed_record_count + 40 + _spine_records(floor) + 5
    )
    assert store.parsed_record_count < count // 2


def test_live_append_keeps_existing_tail_floor(tmp_path):  # noqa: ANN001, ANN201
    parts = _attach(tmp_path, _round_log(200))
    floor = parts.api.snapshot().sequence - 40

    with _live_subscription(parts.api, SubscribeRequest(after_sequence=0, tail=40)) as read:
        read()
        read()
        parts.journal.publish_output("stdout", "one more line")
        batch = read()

    assert batch["history_after_sequence"] == floor
    assert [event["type"] for event in batch["events"]] == ["output"]


def test_wait_for_change_does_not_parse_events(tmp_path):  # noqa: ANN001, ANN201
    parts = _attach(tmp_path, _round_log(200))
    store = parts.journal._store  # noqa: SLF001
    assert store is not None
    parsed = store.parsed_record_count

    assert parts.api.wait_for_change(0, timeout=0.5) is True
    assert parts.api.wait_for_change(parts.api.latest_sequence, timeout=0.01) is False
    assert store.parsed_record_count == parsed
