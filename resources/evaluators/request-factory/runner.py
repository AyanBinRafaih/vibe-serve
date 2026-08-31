# ruff: noqa: INP001
"""Thin process adapter for the pinned Request Factory engine."""

from __future__ import annotations

import os
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

_FORWARD_PREFIX_LENGTH = 3


def main(argv: Sequence[str] | None = None) -> int:
    """Forward evaluator arguments to the framework-provisioned engine."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    if (
        len(arguments) < _FORWARD_PREFIX_LENGTH
        or arguments[0] != "--engine"
        or arguments[2] != "--"
    ):
        raise ValueError("usage: runner.py --engine <path> -- [arguments ...]")  # noqa: TRY003
    engine = arguments[1]
    os.execv(engine, [engine, *arguments[_FORWARD_PREFIX_LENGTH:]])  # noqa: S606
    return 0  # pragma: no cover


if __name__ == "__main__":
    raise SystemExit(main())
