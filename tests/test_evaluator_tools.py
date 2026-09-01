"""Tests for immutable evaluator tool provisioning."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from vibesys.evaluators import (
    CargoGitToolSpec,
    EvaluatorToolError,
    EvaluatorToolLifecycleHandler,
    cargo_install_argv,
    prepare_evaluator_tools,
    tool_install_root,
    tool_spec_digest,
    tool_token,
)
from vs_sandbox import SandboxLifecycle


def _spec() -> CargoGitToolSpec:
    return CargoGitToolSpec(
        kind="cargo-git",
        git="https://example.com/tools",
        rev="1" * 40,
        package="example-package",
        bins=("runner", "tracegen"),
    )


def test_cargo_install_argv_uses_locked_revision_and_positional_package(tmp_path: Path) -> None:
    arguments = cargo_install_argv(_spec(), tmp_path / "install")

    assert arguments == (
        "cargo",
        "install",
        "--git",
        "https://example.com/tools",
        "--rev",
        "1" * 40,
        "--locked",
        "--root",
        str(tmp_path / "install"),
        "--bin",
        "runner",
        "--bin",
        "tracegen",
        "example-package",
    )
    assert "--package" not in arguments


def test_prepare_tools_publishes_complete_install_and_reuses_it(tmp_path: Path) -> None:
    calls: list[tuple[str, ...]] = []

    def install(arguments):  # noqa: ANN001, ANN202
        normalized = tuple(arguments)
        calls.append(normalized)
        root = Path(normalized[normalized.index("--root") + 1])
        for binary in ("runner", "tracegen"):
            path = root / "bin" / binary
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("binary", encoding="utf-8")
            path.chmod(0o755)
        return subprocess.CompletedProcess(normalized, 0, "", "")

    install_parent = tmp_path / "tools"
    first = prepare_evaluator_tools({"example": _spec()}, install_parent, command_runner=install)
    second = prepare_evaluator_tools({"example": _spec()}, install_parent, command_runner=install)

    root = tool_install_root(install_parent, "example", _spec())
    expected = root / "bin" / "runner"
    assert first[tool_token("example", "runner")] == str(expected)
    assert second == first
    assert len(calls) == 1
    assert expected.is_file()
    receipt = json.loads((root / "receipt.json").read_text(encoding="utf-8"))
    assert receipt["spec"] == _spec().model_dump(mode="json")
    assert set(receipt["binaries"]) == {"runner", "tracegen"}
    assert root.name == tool_spec_digest(_spec())
    assert not list(root.parent.glob(f".{root.name}-*"))


def test_lifecycle_handler_snapshots_and_prepares_tool_requirements(tmp_path: Path) -> None:
    calls: list[tuple[str, ...]] = []

    def install(arguments):  # noqa: ANN001, ANN202
        normalized = tuple(arguments)
        calls.append(normalized)
        root = Path(normalized[normalized.index("--root") + 1])
        for binary in ("runner", "tracegen"):
            path = root / "bin" / binary
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("binary", encoding="utf-8")
            path.chmod(0o755)
        return subprocess.CompletedProcess(normalized, 0, "", "")

    tools = {"example": _spec()}
    install_parent = tmp_path / "tools"
    handler = EvaluatorToolLifecycleHandler(
        tools,
        install_parent,
        command_runner=install,
    )
    tools.clear()

    lifecycle = SandboxLifecycle([handler])
    lifecycle.before_ready(MagicMock())
    lifecycle.before_ready(MagicMock())

    assert len(calls) == 1
    assert (tool_install_root(install_parent, "example", _spec()) / "bin" / "runner").is_file()


def test_prepare_tools_rejects_binary_changed_after_receipt(tmp_path: Path) -> None:
    def install(arguments):  # noqa: ANN001, ANN202
        normalized = tuple(arguments)
        root = Path(normalized[normalized.index("--root") + 1])
        for binary in ("runner", "tracegen"):
            path = root / "bin" / binary
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("binary", encoding="utf-8")
            path.chmod(0o755)
        return subprocess.CompletedProcess(normalized, 0, "", "")

    install_parent = tmp_path / "tools"
    prepare_evaluator_tools({"example": _spec()}, install_parent, command_runner=install)
    root = tool_install_root(install_parent, "example", _spec())
    (root / "bin" / "runner").write_text("tampered", encoding="utf-8")

    with pytest.raises(EvaluatorToolError, match="failed receipt verification"):
        prepare_evaluator_tools({"example": _spec()}, install_parent, command_runner=install)


def test_prepare_tools_accepts_verified_concurrent_winner(tmp_path: Path) -> None:
    install_parent = tmp_path / "tools"

    def write_binaries(arguments):  # noqa: ANN001, ANN202
        normalized = tuple(arguments)
        root = Path(normalized[normalized.index("--root") + 1])
        for binary in ("runner", "tracegen"):
            path = root / "bin" / binary
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("binary", encoding="utf-8")
            path.chmod(0o755)
        return subprocess.CompletedProcess(normalized, 0, "", "")

    def publish_winner(arguments):  # noqa: ANN001, ANN202
        result = write_binaries(arguments)
        prepare_evaluator_tools(
            {"example": _spec()},
            install_parent,
            command_runner=write_binaries,
        )
        return result

    replacements = prepare_evaluator_tools(
        {"example": _spec()},
        install_parent,
        command_runner=publish_winner,
    )

    root = tool_install_root(install_parent, "example", _spec())
    assert replacements[tool_token("example", "runner")] == str(root / "bin" / "runner")
    assert json.loads((root / "receipt.json").read_text(encoding="utf-8"))["spec"] == (
        _spec().model_dump(mode="json")
    )


def test_prepare_tools_translates_missing_cargo(tmp_path: Path) -> None:
    def missing(arguments):  # noqa: ANN001, ANN202, ARG001
        raise FileNotFoundError("cargo")

    with pytest.raises(EvaluatorToolError, match="cargo was not found"):
        prepare_evaluator_tools({"example": _spec()}, tmp_path, command_runner=missing)


def test_prepare_tools_reports_cargo_failure_and_cleans_staging(tmp_path: Path) -> None:
    def fail(arguments):  # noqa: ANN001, ANN202
        return subprocess.CompletedProcess(arguments, 7, "", "dependency resolution failed")

    with pytest.raises(EvaluatorToolError, match="dependency resolution failed"):
        prepare_evaluator_tools({"example": _spec()}, tmp_path, command_runner=fail)

    cache = tmp_path / "example"
    assert not list(cache.glob(".*-*"))


def test_prepare_tools_reports_timeout_and_cleans_staging(tmp_path: Path) -> None:
    def timeout(arguments):  # noqa: ANN001, ANN202
        raise subprocess.TimeoutExpired(arguments, 600)

    with pytest.raises(EvaluatorToolError, match="cargo install timed out"):
        prepare_evaluator_tools({"example": _spec()}, tmp_path, command_runner=timeout)

    cache = tmp_path / "example"
    assert not list(cache.glob(".*-*"))
