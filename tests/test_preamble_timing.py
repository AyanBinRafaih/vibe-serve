"""Unit tests for the dispatch-preamble timing collector.

``vibesys.preamble_timing`` buffers ``main stage`` lines recorded by
``_dispatch``/``run_agent_loop`` (main.py, loops/agent/loop.py) before a
``RunLogger`` exists, so ``vibesys.context._assemble_run_context`` can drain
them into the run log. See ``tests/test_context.py`` for the integration
test that exercises the drain through a real ``create_run_context`` call.
"""

from vibesys import preamble_timing


def test_record_stage_prints_to_stderr_and_buffers(capsys):  # noqa: ANN001, ANN201
    preamble_timing.record_stage("main stage example: 1ms")

    captured = capsys.readouterr()
    assert "main stage example: 1ms" in captured.err
    assert preamble_timing.drain_log_lines() == ["main stage example: 1ms"]


def test_drain_log_lines_clears_the_buffer():  # noqa: ANN201
    preamble_timing.record_stage("main stage first: 1ms")
    preamble_timing.drain_log_lines()
    preamble_timing.record_stage("main stage second: 2ms")

    assert preamble_timing.drain_log_lines() == ["main stage second: 2ms"]


def test_record_total_without_start_clock_is_a_noop():  # noqa: ANN201
    preamble_timing.drain_log_lines()  # reset any state left by another test

    preamble_timing.record_total()

    assert preamble_timing.drain_log_lines() == []


def test_record_total_after_start_clock_records_one_line():  # noqa: ANN201
    preamble_timing.start_clock()
    preamble_timing.record_total()

    lines = preamble_timing.drain_log_lines()
    assert len(lines) == 1
    assert lines[0].startswith("dispatch preamble total: ")
    assert lines[0].endswith("ms")


def test_drain_log_lines_resets_the_clock():  # noqa: ANN201
    """A second ``record_total`` with no new ``start_clock`` call is a no-op.

    Otherwise a stale start time from a prior dispatch would leak into an
    unrelated later run's preamble total.
    """
    preamble_timing.start_clock()
    preamble_timing.drain_log_lines()

    preamble_timing.record_total()

    assert preamble_timing.drain_log_lines() == []
