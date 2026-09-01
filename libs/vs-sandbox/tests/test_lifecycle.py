"""Contract tests for backend-independent sandbox lifecycle handling."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

import pytest

from vs_sandbox import (
    BeforeReadyContext,
    SandboxLifecycle,
    SandboxLifecycleError,
    SandboxLifecycleHandler,
)

if TYPE_CHECKING:
    from deepagents.backends.protocol import SandboxBackendProtocol


@dataclass
class _RecordingHandler(SandboxLifecycleHandler):
    name: str
    events: list[tuple[str, object]]

    def before_ready(self, context: BeforeReadyContext) -> None:
        self.events.append((self.name, context.sandbox))


class _FailingHandler(SandboxLifecycleHandler):
    def before_ready(self, context: BeforeReadyContext) -> None:  # noqa: ARG002
        raise ValueError("setup exploded")  # noqa: TRY003


def _sandbox() -> SandboxBackendProtocol:
    return cast("SandboxBackendProtocol", object())


def test_base_handler_is_a_noop() -> None:
    SandboxLifecycle([SandboxLifecycleHandler()]).before_ready(_sandbox())


def test_before_ready_passes_sandbox_to_handlers_in_registration_order() -> None:
    sandbox = _sandbox()
    events: list[tuple[str, object]] = []
    lifecycle = SandboxLifecycle(
        [
            _RecordingHandler("first", events),
            _RecordingHandler("second", events),
        ]
    )

    lifecycle.before_ready(sandbox)

    assert events == [("first", sandbox), ("second", sandbox)]


def test_constructor_snapshots_mutable_handler_sequence() -> None:
    events: list[tuple[str, object]] = []
    handlers: list[SandboxLifecycleHandler] = [_RecordingHandler("first", events)]
    lifecycle = SandboxLifecycle(handlers)
    handlers.append(_RecordingHandler("late", events))

    lifecycle.before_ready(_sandbox())

    assert [name for name, _ in events] == ["first"]
    assert lifecycle.handlers == (handlers[0],)


def test_failure_names_handler_preserves_cause_and_stops_dispatch() -> None:
    events: list[tuple[str, object]] = []
    lifecycle = SandboxLifecycle(
        [
            _FailingHandler(),
            _RecordingHandler("not-run", events),
        ]
    )

    with pytest.raises(
        SandboxLifecycleError,
        match=r"before_ready lifecycle handler _FailingHandler failed: setup exploded",
    ) as error:
        lifecycle.before_ready(_sandbox())

    assert isinstance(error.value.__cause__, ValueError)
    assert events == []
