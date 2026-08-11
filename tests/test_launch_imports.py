"""Import cost of the paths a launch takes before any agent exists.

``deepagents`` pulls the LangChain and Anthropic stacks behind it, seconds of
import time. Only a run that constructs a real agent backend needs them, so
neither the engine entry point nor the modules a stub run touches may import
them at module scope. These tests fail loudly if an annotation-only import
creeps back to runtime.
"""

from __future__ import annotations

import subprocess
import sys

AGENT_STACK = ("deepagents", "langchain", "langchain_core", "anthropic")

_PROBE = """
import importlib
import sys

importlib.import_module({module!r})
loaded = sorted(
    name
    for name in {stack!r}
    if any(module == name or module.startswith(name + ".") for module in sys.modules)
)
print(",".join(loaded))
"""


def _agent_stack_after_importing(module: str) -> list[str]:
    result = subprocess.run(  # noqa: S603  # tracked: #288
        [sys.executable, "-c", _PROBE.format(module=module, stack=AGENT_STACK)],
        capture_output=True,
        text=True,
        check=True,
    )
    return [name for name in result.stdout.strip().split(",") if name]


def test_engine_entry_point_does_not_import_the_agent_stack() -> None:
    """``python -m vibesys`` imports this module before it parses a flag."""
    assert _agent_stack_after_importing("vibesys.main") == []


def test_stub_runner_does_not_import_the_agent_stack() -> None:
    """The stub exists to run without an agent stack; it must not import one."""
    assert _agent_stack_after_importing("vibesys.agents.stub_runner") == []


def test_backend_registry_does_not_import_the_agent_stack() -> None:
    """Naming a compute backend is not constructing one."""
    assert _agent_stack_after_importing("vibesys.backends") == []


def test_run_environment_does_not_import_the_agent_stack() -> None:
    assert _agent_stack_after_importing("vibesys.sandbox.run_environment") == []


def test_backend_registry_still_resolves_every_default(tmp_path) -> None:  # noqa: ANN001  # tracked: #288
    """Deferring registration must not lose a backend."""
    from vibesys import backends  # noqa: PLC0415  # tracked: #288
    from vibesys.constants import ComputeBackend  # noqa: PLC0415  # tracked: #288

    for backend in (
        ComputeBackend.CPU,
        ComputeBackend.METAL,
        ComputeBackend.CUDA,
        ComputeBackend.ROCM,
        ComputeBackend.TRAINIUM,
    ):
        assert backends.get(backend, log_dir=tmp_path) is not None
