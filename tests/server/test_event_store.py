"""Persistence and cursor tests for the run-event store."""

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path

import pytest
from pydantic import ValidationError

from vibesys.server.events import EventStore, EventType, RunEvent, ToolCallData, make_event


def _persisted_event(sequence: int, text: str = "") -> RunEvent:
    return RunEvent(
        sequence=sequence,
        run_id="persisted-run",
        timestamp=datetime.now(UTC),
        type=EventType.OUTPUT,
        text=text,
    )


def _write_events(path: Path, events: list[RunEvent]) -> None:
    path.write_text("".join(event.model_dump_json() + "\n" for event in events))


class TestEventStore:
    def test_startup_replay_cursor_and_next_sequence(self, tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
        path = tmp_path / "events.jsonl"
        _write_events(path, [_persisted_event(1, "one"), _persisted_event(2, "two")])

        store = EventStore(path, run_id="active-run")

        assert [event.text for event in store.read()] == ["one", "two"]
        assert [event.text for event in store.read(after_sequence=1)] == ["two"]
        assert store.read(after_sequence=2) == []
        appended = store.append(make_event(EventType.OUTPUT, "three"))
        assert appended.sequence == 3
        assert appended.run_id == "active-run"
        assert [event.sequence for event in store.read(after_sequence=1)] == [2, 3]

    def test_legacy_out_of_order_sequences_get_stable_monotonic_cursors(self, tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
        path = tmp_path / "events.jsonl"
        _write_events(
            path,
            [_persisted_event(2, "two"), _persisted_event(1, "one"), _persisted_event(3, "three")],
        )
        original = path.read_bytes()

        store = EventStore(path, run_id="active-run")

        assert [(event.sequence, event.text) for event in store.read()] == [
            (2, "two"),
            (3, "one"),
            (4, "three"),
        ]
        assert path.read_bytes() == original
        assert [event.text for event in store.read(after_sequence=2)] == ["one", "three"]
        assert store.append(make_event(EventType.OUTPUT, "four")).sequence == 5

        reopened = EventStore(path, run_id="reopened-run")
        assert [(event.sequence, event.text) for event in reopened.read()] == [
            (2, "two"),
            (3, "one"),
            (4, "three"),
            (5, "four"),
        ]

    def test_legacy_duplicate_sequences_preserve_every_payload(self, tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
        path = tmp_path / "events.jsonl"
        _write_events(
            path,
            [
                _persisted_event(1, "one"),
                _persisted_event(2, "first two"),
                _persisted_event(2, "second two"),
                _persisted_event(3, "three"),
            ],
        )

        store = EventStore(path, run_id="active-run")

        assert [(event.sequence, event.text) for event in store.read()] == [
            (1, "one"),
            (2, "first two"),
            (3, "second two"),
            (4, "three"),
        ]
        assert [event.text for event in store.read(after_sequence=2)] == [
            "second two",
            "three",
        ]

    def test_legacy_sequence_resets_replay_every_payload_across_cursors(self, tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
        path = tmp_path / "events.jsonl"
        raw_sequences = [*range(7690, 7711), *range(7690, 7715), *range(7711, 7738)]
        _write_events(
            path,
            [
                _persisted_event(sequence, f"payload-{index}")
                for index, sequence in enumerate(raw_sequences)
            ],
        )
        store = EventStore(path, run_id="active-run")

        replayed: list[RunEvent] = []
        cursor = 0
        while batch := store.read(after_sequence=cursor)[:7]:
            replayed.extend(batch)
            cursor = batch[-1].sequence

        assert [event.text for event in replayed] == [
            f"payload-{index}" for index in range(len(raw_sequences))
        ]
        assert all(previous.sequence < current.sequence for previous, current in pairwise(replayed))
        assert store.last_sequence == replayed[-1].sequence

    def test_repeated_tail_reads_do_not_reparse_history(self, tmp_path, monkeypatch):  # noqa: ANN001, ANN201  # tracked: #288
        path = tmp_path / "events.jsonl"
        event_count = 1_000
        _write_events(path, [_persisted_event(index) for index in range(1, event_count + 1)])
        parse_count = 0
        parse = RunEvent.model_validate_json

        def counting_parse(raw):  # noqa: ANN001, ANN202  # tracked: #288
            nonlocal parse_count
            parse_count += 1
            return parse(raw)

        monkeypatch.setattr(RunEvent, "model_validate_json", counting_parse)
        store = EventStore(path, run_id="active-run")
        assert parse_count == event_count

        for _ in range(20):
            assert store.read(event_count) == []
            assert store.wait(event_count, timeout=0) == []

        assert parse_count == event_count

    def test_ignores_only_a_malformed_final_record(self, tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
        path = tmp_path / "events.jsonl"
        valid = _persisted_event(1, "complete").model_dump_json()
        path.write_text(valid + "\n" + '{"protocol_version":1')

        store = EventStore(path, run_id="active-run")

        assert [event.text for event in store.read()] == ["complete"]

    @pytest.mark.parametrize("tail", ['{"protocol_version":1', '{"protocol_version":1\n'])
    def test_append_repairs_ignored_malformed_tail_before_writing(self, tmp_path, tail):  # noqa: ANN001, ANN201  # tracked: #288
        path = tmp_path / "events.jsonl"
        valid = _persisted_event(1, "complete").model_dump_json()
        path.write_text(valid + "\n" + tail)
        store = EventStore(path, run_id="active-run")

        store.append(make_event(EventType.OUTPUT, "after repair"))
        reopened = EventStore(path, run_id="reopened-run")

        assert [(event.sequence, event.text) for event in reopened.read()] == [
            (1, "complete"),
            (2, "after repair"),
        ]

    def test_rejects_a_malformed_record_before_the_tail(self, tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
        path = tmp_path / "events.jsonl"
        first = _persisted_event(1).model_dump_json()
        last = _persisted_event(2).model_dump_json()
        path.write_text(first + "\nnot-json\n" + last + "\n")

        with pytest.raises(ValueError):  # noqa: PT011  # tracked: #288
            EventStore(path, run_id="active-run")

    def test_append_wakes_multiple_independent_readers(self, tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
        store = EventStore(tmp_path / "events.jsonl", run_id="active-run")
        ready = threading.Barrier(3)

        def wait_for_first_event() -> list[RunEvent]:
            ready.wait()
            return store.wait(after_sequence=0, timeout=2)

        with ThreadPoolExecutor(max_workers=2) as executor:
            readers = [executor.submit(wait_for_first_event) for _ in range(2)]
            ready.wait()
            appended = store.append(make_event(EventType.OUTPUT, "visible"))

        batches = [reader.result() for reader in readers]
        assert [[event.sequence for event in batch] for batch in batches] == [[1], [1]]
        assert all(batch[0] == appended for batch in batches)
        # Readers share the stored event rather than each getting a copy: it is
        # frozen, so sharing is what keeps replay from copying whole histories.
        assert batches[0][0] is batches[1][0]
        assert batches[0] is not batches[1]

    def test_cached_events_are_isolated_from_reader_mutation(self, tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
        store = EventStore(tmp_path / "events.jsonl", run_id="active-run")
        appended = store.append(make_event(EventType.OUTPUT, "durable"))

        first_read = store.read()
        with pytest.raises(ValidationError):
            first_read[0].text = "mutated"

        assert appended.text == "durable"
        assert store.read()[0].text == "durable"

    def test_reader_projections_do_not_disturb_stored_history(self, tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
        """The read path builds variants by copy, which the store never sees."""
        store = EventStore(tmp_path / "events.jsonl", run_id="active-run")
        store.append(make_event(EventType.OUTPUT, "durable"))

        projected = store.read()[0].model_copy(update={"text": "projected"})

        assert projected.text == "projected"
        assert store.read()[0].text == "durable"

    def test_appended_events_are_immutable(self, tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
        store = EventStore(tmp_path / "events.jsonl", run_id="active-run")
        appended = store.append(
            make_event(EventType.TOOL_CALL, data=ToolCallData(tool="Bash", call_id="c1"))
        )

        with pytest.raises(ValidationError):
            appended.sequence = 99
        assert isinstance(appended.data, ToolCallData)
        with pytest.raises(ValidationError):
            appended.data.tool = "Write"

    def test_append_does_not_publish_cache_state_when_file_close_fails(self, tmp_path, monkeypatch):  # noqa: ANN001, ANN201  # tracked: #288
        path = tmp_path / "events.jsonl"
        store = EventStore(path, run_id="active-run")
        real_open = Path.open

        class FailingCloseStream:
            def __enter__(self):  # noqa: ANN204  # tracked: #288
                return self

            def write(self, _text):  # noqa: ANN001, ANN202  # tracked: #288
                return None

            def __exit__(self, *_args):  # noqa: ANN002, ANN204  # tracked: #288
                raise OSError("close failed")  # noqa: TRY003  # tracked: #288

        def open_with_close_failure(target, mode="r", *args, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN202  # tracked: #288
            if target == path and mode == "a":
                return FailingCloseStream()
            return real_open(target, mode, *args, **kwargs)

        monkeypatch.setattr(Path, "open", open_with_close_failure)

        with pytest.raises(OSError, match="close failed"):
            store.append(make_event(EventType.OUTPUT, "ghost"))

        assert store.read() == []
        assert store.last_sequence == 0
