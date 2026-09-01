# ruff: noqa: INP001
"""Launch a task-owned Request Factory adapter with the trusted engine path."""

from __future__ import annotations

import os
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

_FORWARD_PREFIX_LENGTH = 3


def main(argv: Sequence[str] | None = None) -> int:
    """Inject the installed engine into one task-owned benchmark adapter."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    if (
        len(arguments) <= _FORWARD_PREFIX_LENGTH
        or arguments[0] != "--engine"
        or arguments[2] != "--"
    ):
        raise ValueError(  # noqa: TRY003
            "usage: adapter.py --engine <path> -- <script> [arguments ...]"
        )
    engine = arguments[1]
    script = arguments[3]
    script_arguments = arguments[4:]
    os.execv(  # noqa: S606
        sys.executable,
        [
            sys.executable,
            script,
            "--request-factory-engine",
            engine,
            *script_arguments,
        ],
    )
    return 0  # pragma: no cover
