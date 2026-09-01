"""Trusted lifecycle extensions for sandbox startup.

Lifecycle handlers are registered by framework code, not by candidate code.
The sandbox invokes :meth:`SandboxLifecycleHandler.before_ready` after its
execution environment accepts commands and before it is exposed to callers.
Handlers run again whenever a backend creates a replacement execution
environment, so implementations must be idempotent.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from deepagents.backends.protocol import SandboxBackendProtocol


@dataclass(frozen=True)
class BeforeReadyContext:
    """Resources available while a sandbox is transitioning to ready."""

    sandbox: SandboxBackendProtocol


class SandboxLifecycleHandler:
    """Base class for trusted sandbox lifecycle extensions.

    Future lifecycle points can be added here as concrete no-op methods. That
    keeps existing subclasses compatible while allowing one handler instance
    to participate in more than one phase.
    """

    def before_ready(self, context: BeforeReadyContext) -> None:
        """Prepare an execution-capable sandbox before callers can use it."""


class SandboxLifecycleError(RuntimeError):
    """Raised when a lifecycle handler prevents a sandbox becoming ready."""

    def __init__(self, hook: str, handler: str, cause: Exception) -> None:
        """Name the failed hook and handler while retaining the cause."""
        super().__init__(f"{hook} lifecycle handler {handler} failed: {cause}")


class SandboxLifecycle:
    """Run an ordered, immutable snapshot of lifecycle handlers."""

    def __init__(
        self,
        handlers: Sequence[SandboxLifecycleHandler] | None = None,
    ) -> None:
        """Snapshot handlers in their deterministic execution order."""
        self._handlers = tuple(handlers or ())

    @property
    def handlers(self) -> tuple[SandboxLifecycleHandler, ...]:
        """Return the handlers in their deterministic execution order."""
        return self._handlers

    def before_ready(self, sandbox: SandboxBackendProtocol) -> None:
        """Run every handler, stopping at the first failure."""
        context = BeforeReadyContext(sandbox=sandbox)
        for handler in self._handlers:
            try:
                handler.before_ready(context)
            except Exception as exc:
                name = type(handler).__name__
                raise SandboxLifecycleError("before_ready", name, exc) from exc
