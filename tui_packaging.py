"""Build and stage the OpenTUI client for inclusion in the Python wheel.

The interactive TUI lives in ``clients/tui`` as a TypeScript project whose only
runtime dependency is the native ``@opentui/core`` renderer. To make a single
``pip install`` deliver a usable TUI, the wheel build compiles the TypeScript
and vendors the (platform-matched) ``node_modules`` into the ``vibesys._tui``
package directory. ``setup.py`` calls :func:`build_and_stage_tui` from a custom
``build_py`` step.

The step is **best-effort**: when no JavaScript toolchain is available, or the
build fails, it logs a warning and returns ``False`` so the wheel still installs
a fully functional headless engine. It is deliberately dependency-free (only the
standard library) so it imports cleanly inside an isolated build environment.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path  # noqa: TC003  # tracked: #288

#: JavaScript package managers tried in preference order. Bun matches the TUI's
#: runtime and is fastest; npm ships with Node and is the ubiquitous fallback.
#: pnpm is intentionally omitted: a scoped install inside this repo's pnpm
#: workspace pulls in workspace resolution we do not want at package-build time.
_PACKAGE_MANAGERS: tuple[str, ...] = ("bun", "npm")

#: What gets copied from ``clients/tui`` into the staged ``_tui`` package dir.
_STAGED_ENTRIES: tuple[str, ...] = ("dist", "node_modules", "package.json")

# The return value is ignored (we only care about exit status via ``check``),
# so accept any callable shaped like ``subprocess.run`` regardless of return.
Runner = Callable[..., object]
Which = Callable[[str], str | None]


def detect_package_manager(*, which: Which = shutil.which) -> str | None:
    """Return the first available package manager, or ``None`` if none exist."""
    for manager in _PACKAGE_MANAGERS:
        if which(manager) is not None:
            return manager
    return None


def install_command(manager: str) -> tuple[str, ...]:
    """Argv that installs the TUI's dependencies (all, including dev)."""
    if manager == "bun":
        return ("bun", "install")
    return ("npm", "install", "--no-audit", "--no-fund")


def build_command(manager: str) -> tuple[str, ...]:
    """Argv that runs the ``build`` script (``tsc``) to emit ``dist/``."""
    return (manager, "run", "build")


def prune_command(manager: str) -> tuple[str, ...]:
    """Argv that drops dev-only dependencies, leaving the runtime set."""
    if manager == "bun":
        return ("bun", "install", "--production")
    return ("npm", "prune", "--omit=dev")


def _log(message: str) -> None:
    print(f"[vibesys build] {message}", file=sys.stderr)  # noqa: T201  # tracked: #288


def _run(command: Sequence[str], *, cwd: Path, runner: Runner) -> None:
    _log(f"$ {' '.join(command)}  (in {cwd})")
    runner(list(command), cwd=str(cwd), check=True)


def build_and_stage_tui(
    repo_root: Path,
    dest: Path,
    *,
    which: Which = shutil.which,
    runner: Runner = subprocess.run,
) -> bool:
    """Compile the TUI and copy ``dist`` + ``node_modules`` into ``dest``.

    Returns ``True`` when the staged bundle is ready, ``False`` when the build
    was skipped (no toolchain) or failed. Never raises for a build problem: a
    missing TUI degrades to headless-only, it does not break ``pip install``.
    """
    tui_src = repo_root / "clients" / "tui"
    if not (tui_src / "package.json").is_file():
        _log(f"no TUI project at {tui_src}; skipping TUI build (headless only).")
        return False

    manager = detect_package_manager(which=which)
    if manager is None:
        _log(
            "no JavaScript toolchain (Bun or Node+npm) found; skipping TUI build. "
            "The engine still installs and runs headless via `python -m vibesys`."
        )
        return False

    try:
        _run(install_command(manager), cwd=tui_src, runner=runner)
        _run(build_command(manager), cwd=tui_src, runner=runner)
        _run(prune_command(manager), cwd=tui_src, runner=runner)
    except (subprocess.CalledProcessError, OSError) as exc:
        _log(f"TUI build failed ({exc}); installing headless-only.")
        return False

    dist_dir = tui_src / "dist"
    if not (dist_dir / "launcher.js").is_file():
        _log("TUI build produced no dist/launcher.js; installing headless-only.")
        return False

    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    # Source maps are debug-only and add several MB; the runtime never reads them.
    ignore = shutil.ignore_patterns("*.map")
    for entry in _STAGED_ENTRIES:
        source = tui_src / entry
        if not source.exists():
            continue
        target = dest / entry
        if source.is_dir():
            shutil.copytree(source, target, symlinks=True, ignore=ignore)
        else:
            shutil.copy2(source, target)

    _log(f"staged TUI bundle into {dest}")
    return True
