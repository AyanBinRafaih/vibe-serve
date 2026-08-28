"""Timing collector for the dispatch preamble that runs before a run log exists.

``_dispatch`` (``main.py``) and ``run_agent_loop``
(``loops/agent/loop.py``) do substantial work — config/skill loading,
experiment repository setup, resume restoration, run-environment resolution
— between parsing the CLI invocation and calling
:func:`vibesys.context.create_run_context`. There is no ``RunLogger`` yet to
receive diagnostics for that span.

This module has no VibeSys imports of its own, so it stays import-cheap for
early callers: ``main.py`` can record timing from ``_dispatch`` without
pulling in the framework packages that ``vibesys.context`` depends on
(``vibesys.agents``, ``vibesys.backends``, ...).

Call sites record ``main stage <name>: <ms>ms`` lines as work happens
(printed to stderr immediately, matching how every pre-logger diagnostic in
``vibesys.context`` is surfaced). ``vibesys.context._assemble_run_context``
drains them into its own pre-logger buffer at assembly entry, so they land
in the persistent run log alongside the ``context stage`` lines it already
produces.
"""

import sys
import time

_log_lines: list[str] = []
_started_at: float | None = None


def start_clock() -> None:
    """Mark the start of the dispatch preamble, for :func:`record_total`."""
    global _started_at  # noqa: PLW0603  # tracked: #288
    _started_at = time.perf_counter()


def record_stage(message: str) -> None:
    """Record one dispatch-preamble timing line and print it immediately."""
    print(message, file=sys.stderr)  # noqa: T201  # tracked: #288
    _log_lines.append(message)


def record_total() -> None:
    """Record the total dispatch-preamble duration, right before context assembly.

    No-op if :func:`start_clock` was never called (e.g. tests that build a
    run context directly, bypassing ``_dispatch``).
    """
    if _started_at is None:
        return
    elapsed_ms = (time.perf_counter() - _started_at) * 1000
    record_stage(f"dispatch preamble total: {elapsed_ms:.0f}ms")


def drain_log_lines() -> list[str]:
    """Pop every buffered dispatch-preamble line for inclusion in the run log."""
    global _started_at  # noqa: PLW0603  # tracked: #288
    lines = list(_log_lines)
    _log_lines.clear()
    _started_at = None
    return lines
