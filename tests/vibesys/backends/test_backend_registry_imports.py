"""Registering compute backends must not pull the agent runtime's heavy imports.

``backends.get`` runs on the startup path of every entry point, before the TUI
can list experiments. If registering the default backends imported
``deepagents`` (which transitively imports langchain and the provider SDKs),
that startup paid seconds of import time it does not need: the sandbox classes
are only required once a sandbox is actually constructed.

The probe runs in a subprocess because import cost is a property of a fresh
interpreter, and the pytest session has usually imported deepagents already.
"""

import subprocess
import sys
from pathlib import Path

from deepagents.backends import LocalShellBackend

from vibesys.backends import SandboxKind
from vibesys.backends.local import cpu_backend

_PROBE = """
import sys

from vibesys import backends
from vibesys.constants import ComputeBackend

backends.get(ComputeBackend.{backend}, log_dir={log_dir!r})
roots = {{name.split(".")[0] for name in sys.modules}}
heavy = {{root for root in roots if root == "deepagents" or root.startswith("langchain")}}
print(",".join(sorted(heavy)))
"""


def _heavy_modules_after_backend_construction(backend: str, log_dir: Path) -> set[str]:
    result = subprocess.run(  # noqa: S603  # tracked: #288
        [sys.executable, "-c", _PROBE.format(backend=backend, log_dir=str(log_dir))],
        capture_output=True,
        text=True,
        check=True,
    )
    loaded = result.stdout.strip()
    return set(loaded.split(",")) if loaded else set()


def test_constructing_a_compute_backend_does_not_import_the_agent_runtime(tmp_path: Path) -> None:
    """Backend construction stays import-cheap for every registered backend.

    Registration imports all backend modules, so one backend is enough to catch
    a module-level ``deepagents`` import in any of them.
    """
    assert _heavy_modules_after_backend_construction("CUDA", tmp_path) == set()


def test_local_sandbox_construction_still_reaches_deepagents(tmp_path: Path) -> None:
    """The deferred import is a deferral, not a removal: the sandbox still works."""
    sandbox = cpu_backend(log_dir=tmp_path).make_sandbox(
        SandboxKind.LOCAL,
        host_workspace=str(tmp_path),
        log_path=None,
    )

    assert isinstance(sandbox, LocalShellBackend)
