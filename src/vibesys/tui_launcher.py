"""Launch the bundled OpenTUI client from an installed ``vibesys`` package.

When VibeSys is installed from source with a JavaScript toolchain present, the
wheel carries a prebuilt TUI under ``vibesys/_tui`` (see ``tui_packaging.py``).
This module is the ``vibesys-tui`` console entry point: it locates that bundle,
picks a JavaScript runtime to execute the launcher, points the launcher at the
current Python interpreter, and hands off.

The compiled ``dist/launcher.js`` owns all interactive orchestration (it spawns
``python -m vibesys`` for the backend and Bun for the OpenTUI frontend). This
module only bootstraps it, so the Python side stays thin.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from importlib.resources import files
from pathlib import Path

#: Runtimes able to execute ``dist/launcher.js``. Bun is preferred (it is also
#: what the launcher uses for the OpenTUI frontend); Node can run the launcher
#: for headless/validate paths that never start the frontend.
_LAUNCHER_RUNTIMES: tuple[str, ...] = ("bun", "node")


def bundled_tui_dir() -> Path | None:
    """Return the staged ``_tui`` bundle directory, or ``None`` if not built in."""
    try:
        base = Path(str(files("vibesys"))) / "_tui"
    except (ModuleNotFoundError, TypeError):  # pragma: no cover - defensive
        return None
    return base if (base / "dist" / "launcher.js").is_file() else None


def _find_runtime() -> str | None:
    for runtime in _LAUNCHER_RUNTIMES:
        if shutil.which(runtime) is not None:
            return runtime
    return None


def _missing_bundle_message() -> str:
    return (
        "vibesys-tui: the interactive TUI is not bundled in this install.\n"
        "Reinstall from source with a JavaScript toolchain (Bun, or Node with npm)\n"
        "available so the build can compile it, or run headless instead:\n"
        "  python -m vibesys --input <bundle> ..."
    )


def _missing_runtime_message() -> str:
    return (
        "vibesys-tui: a JavaScript runtime is required to launch the TUI.\n"
        "Install Bun (https://bun.sh) or Node.js 20+, or run headless instead:\n"
        "  python -m vibesys --input <bundle> ..."
    )


def main(argv: list[str] | None = None) -> int:
    """Entry point for the ``vibesys-tui`` console script."""
    args = list(sys.argv[1:] if argv is None else argv)

    tui_dir = bundled_tui_dir()
    if tui_dir is None:
        print(_missing_bundle_message(), file=sys.stderr)  # noqa: T201  # tracked: #288
        return 1

    runtime = _find_runtime()
    if runtime is None:
        print(_missing_runtime_message(), file=sys.stderr)  # noqa: T201  # tracked: #288
        return 1

    launcher = tui_dir / "dist" / "launcher.js"
    env = {**os.environ, "VIBESYS_PYTHON": sys.executable}
    return subprocess.call([runtime, str(launcher), *args], env=env)  # noqa: S603  # tracked: #288


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
