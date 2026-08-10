"""Setuptools shim that builds and vendors the OpenTUI client into the wheel.

Project metadata lives in ``pyproject.toml``; this file exists only to hook a
custom ``build_py`` step that compiles ``clients/tui`` and stages the result
into ``vibesys/_tui`` so a single ``pip install`` ships a usable TUI. The step
is best-effort — see ``tui_packaging.build_and_stage_tui`` — so installs without
a JavaScript toolchain still succeed and run headless.
"""

from __future__ import annotations

import sys
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py as _build_py

_REPO_ROOT = Path(__file__).parent.resolve()
sys.path.insert(0, str(_REPO_ROOT))

from tui_packaging import build_and_stage_tui  # noqa: E402


class build_py(_build_py):  # noqa: N801 - setuptools command classes are lowercase
    """Standard ``build_py`` plus staging the compiled TUI into the package."""

    def run(self) -> None:  # noqa: D102  # tracked: #288
        super().run()
        dest = Path(self.build_lib) / "vibesys" / "_tui"
        build_and_stage_tui(_REPO_ROOT, dest)


setup(cmdclass={"build_py": build_py})
