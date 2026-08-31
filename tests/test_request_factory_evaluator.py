"""Tests for the bundled Request Factory process adapters."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from types import ModuleType

_PACKAGE_ROOT = Path(__file__).parents[1] / "resources" / "evaluators" / "request-factory"


def _load_script(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"request_factory_{name}", _PACKAGE_ROOT / name)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_engine_runner_execs_binary_after_explicit_separator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _load_script("runner.py")
    captured: list[tuple[str, list[str]]] = []
    monkeypatch.setattr(runner.os, "execv", lambda path, argv: captured.append((path, argv)))

    engine_arguments = [
        "--trace",
        "trace.jsonl",
        "--model",
        "m",
        "--input-file-format",
        "multimodal-independent-v1",
        "--dry-run",
    ]
    assert runner.main(["--engine", "/trusted/session_runner", "--", *engine_arguments]) == 0
    assert captured == [
        (
            "/trusted/session_runner",
            ["/trusted/session_runner", *engine_arguments],
        )
    ]


def test_task_adapter_injects_engine_before_task_arguments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _load_script("adapter.py")
    captured: list[tuple[str, list[str]]] = []
    monkeypatch.setattr(adapter.os, "execv", lambda path, argv: captured.append((path, argv)))

    assert (
        adapter.main(
            ["--engine", "/trusted/session_runner", "--", "benchmark.py", "--url", "server"]
        )
        == 0
    )
    assert captured == [
        (
            sys.executable,
            [
                sys.executable,
                "benchmark.py",
                "--request-factory-engine",
                "/trusted/session_runner",
                "--url",
                "server",
            ],
        )
    ]


@pytest.mark.parametrize(
    "arguments",
    [
        ["--engine", "/trusted/session_runner"],
        ["--engine", "/trusted/session_runner", "--"],
        ["--engine", "/trusted/session_runner", "benchmark.py"],
    ],
)
def test_task_adapter_rejects_missing_separator_or_script(arguments: list[str]) -> None:
    adapter = _load_script("adapter.py")

    with pytest.raises(ValueError, match=r"usage: adapter\.py"):
        adapter.main(arguments)
