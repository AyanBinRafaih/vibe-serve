"""Compute backend protocol — the contract every compute target implements.

A ``ComputeBackendImpl`` knows how to:

1. Construct a sandbox configured for its compute platform
   (image, GPU runtime args, env vars are all internal to the backend).
2. Optionally watch the platform for issues (CUDA: nvidia-smi contention).
3. Optionally migrate compute mid-run (CUDA: re-pick a less-loaded GPU).

Sandbox classes (``DockerSandbox``, ``ModalSandbox``, ``LocalShellBackend``)
stay backend-agnostic: they accept image/env/gpus as plain parameters.  The
compute backend supplies the right values for its platform inside
``make_sandbox``.
"""

from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum
from pathlib import Path  # noqa: TC003  # tracked: #288
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from vibesys.constants import ComputeBackend  # noqa: TC001  # tracked: #288
from vibesys.profilers import ProfilerKind  # noqa: TC001  # tracked: #288

if TYPE_CHECKING:
    # Annotation only; deepagents pulls langchain + anthropic (~seconds).
    from deepagents.backends.protocol import SandboxBackendProtocol
    from deepagents.backends.sandbox import BaseSandbox


class SandboxKind(StrEnum):
    """Where the agent's shell commands actually execute."""

    LOCAL = "local"
    DOCKER = "docker"
    MODAL = "modal"


class Device(Protocol):
    """Minimum device interface ``_RunContext`` consumes for logging and pinning."""

    index: int
    name: str


class ContentionMonitor(Protocol):
    """Background thread that reports platform contention (e.g. shared-GPU use)."""

    def start(self) -> None: ...  # noqa: D102  # tracked: #288
    def stop(self) -> None: ...  # noqa: D102  # tracked: #288


type SetupFn = Callable[[BaseSandbox], None]
"""A function the sandbox runs after every ``start()`` (initial or restart).

Use it to install setup that doesn't survive container restart and that the
sandbox class itself doesn't know about — e.g. ``ln -sfn`` symlinks pointing
into HuggingFace-cache-style bind mounts.
"""


@runtime_checkable
class ComputeBackendImpl(Protocol):
    """Per-platform backend.  See module docstring for the contract."""

    name: ComputeBackend
    profiler_kind: ProfilerKind  # picks profiler support, MCP, and prompt template

    def make_sandbox(  # noqa: PLR0913  # tracked: #288
        self,
        kind: SandboxKind,
        *,
        host_workspace: str,
        log_path: Path | str | None,
        bind_mounts: list[tuple[str, str, bool]],
        passthrough_paths: list[str],
        extra_env: dict[str, str],
        extra_init_commands: list[str],
        setup_fns: list[SetupFn] | None = None,
        modal_options: ModalOptions | None = None,
        attach_accelerator: bool = True,
    ) -> SandboxBackendProtocol:
        """Construct (do not start) a sandbox configured for this backend.

        ``setup_fns`` are invoked by the sandbox at the end of every
        ``start()`` — initial and restart alike.

        ``attach_accelerator=False`` creates a CPU-only control-plane sandbox
        while preserving the target backend's image and tooling. Remote
        dispatch environments use this for local editor containers.
        """
        ...

    def make_monitor(self, log_dir: Path) -> ContentionMonitor | None: ...  # noqa: D102  # tracked: #288

    def reselect_device(self) -> None:
        """Re-pick the optimal device for this backend (e.g. migrate to a
        less-loaded GPU) and restart affected sandboxes in place.

        Each restarted sandbox re-runs its ``setup_fns`` automatically as
        part of ``start()``.  No-op for backends without rebalancing.
        """  # noqa: D205  # tracked: #288
        ...


def make_local_shell_sandbox(
    *,
    host_workspace: str,
    env: dict[str, str],
) -> SandboxBackendProtocol:
    """Construct deepagents' local-shell sandbox, importing deepagents on first use.

    Every backend builds the local sandbox the same way, so the construction
    lives here once. The import is deferred because ``deepagents`` pulls
    langchain + anthropic (seconds on a cold import) and ``backends.get`` runs
    on the startup path, before an application can list experiments.
    """
    from deepagents.backends import LocalShellBackend  # noqa: PLC0415  # tracked: #288

    return LocalShellBackend(
        root_dir=host_workspace,
        virtual_mode=True,
        inherit_env=True,
        env=env,
    )


class ModalOptions:
    """User-supplied Modal sandbox knobs — orthogonal to platform choice.

    The compute backend supplies image and GPU spec; the user supplies runtime
    knobs (lifetime, idle timeout, app, model volume).  Plain attribute
    container so future backends can ignore it without typing gymnastics.
    """

    def __init__(  # noqa: D107, PLR0913  # tracked: #288
        self,
        *,
        gpu: str | None = "H100",
        sandbox_timeout: int = 14400,
        idle_timeout: int | None = 1800,
        model_volume_name: str | None = None,
        extra_readonly_volumes: dict[str, str] | None = None,
        extra_writable_volumes: dict[str, str] | None = None,
        app_name: str = "vibesys",
    ) -> None:
        self.gpu = gpu
        self.sandbox_timeout = sandbox_timeout
        self.idle_timeout = idle_timeout
        self.model_volume_name = model_volume_name
        self.extra_readonly_volumes = extra_readonly_volumes
        self.extra_writable_volumes = extra_writable_volumes
        self.app_name = app_name
