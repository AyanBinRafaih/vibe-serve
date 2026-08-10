"""The ``vibesys`` console entry point.

``vibesys`` is the installed-package equivalent of the in-repo ``./vs`` launcher.
By default it starts the interactive OpenTUI client (bundled into the wheel when
built with a JavaScript toolchain — see ``tui_packaging.py``). It routes to the
headless engine, with no JavaScript runtime required, when:

* ``--headless`` is passed,
* the first argument is ``validate``, or
* stdin/stdout is not a TTY (pipes, CI).

The headless path runs ``python -m vibesys`` in a subprocess; the interactive
path runs the compiled ``dist/launcher.js`` under Bun/Node with ``VIBESYS_PYTHON``
set so the launcher drives the current interpreter.
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
#: for paths that never start the frontend.
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


def _headless_requested(args: list[str]) -> bool:
    """Whether to run the engine directly instead of the interactive TUI."""
    if "--headless" in args:
        return True
    if args and args[0] == "validate":
        return True
    return not (sys.stdin.isatty() and sys.stdout.isatty())


def _run_headless(args: list[str]) -> int:
    return subprocess.call([sys.executable, "-m", "vibesys", *args])  # noqa: S603  # tracked: #288


def _missing_runtime_message() -> str:
    return (
        "vibesys: a JavaScript runtime is required to launch the interactive TUI.\n"
        "Install Bun (https://bun.sh) or Node.js 20+, or run headless instead:\n"
        "  vibesys --headless --input <bundle> ..."
    )


def main(argv: list[str] | None = None) -> int:
    """Entry point for the ``vibesys`` console script."""
    args = list(sys.argv[1:] if argv is None else argv)

    if _headless_requested(args):
        return _run_headless(args)

    tui_dir = bundled_tui_dir()
    if tui_dir is None:
        # No TUI was built into this install; do the useful thing rather than
        # failing, and say why.
        print(  # noqa: T201  # tracked: #288
            "vibesys: interactive TUI is not bundled in this install; running "
            "headless. Reinstall from source with a JavaScript toolchain to get "
            "the TUI, or pass --headless to silence this notice.",
            file=sys.stderr,
        )
        return _run_headless(args)

    runtime = _find_runtime()
    if runtime is None:
        print(_missing_runtime_message(), file=sys.stderr)  # noqa: T201  # tracked: #288
        return 1

    launcher = tui_dir / "dist" / "launcher.js"
    env = {**os.environ, "VIBESYS_PYTHON": sys.executable}
    return subprocess.call([runtime, str(launcher), *args], env=env)  # noqa: S603  # tracked: #288


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
