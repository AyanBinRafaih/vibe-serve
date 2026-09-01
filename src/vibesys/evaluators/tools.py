"""Install immutable external tools declared by evaluator packages."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from vibesys.evaluators.packages import CargoGitToolSpec, tool_token
from vs_sandbox import BeforeReadyContext, SandboxLifecycleHandler

ToolCommandRunner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


class EvaluatorToolError(RuntimeError):
    """Raised when an evaluator tool cannot be prepared safely."""


class EvaluatorToolLifecycleHandler(SandboxLifecycleHandler):
    """Install evaluator-declared tools while a sandbox becomes ready."""

    def __init__(
        self,
        tools: Mapping[str, CargoGitToolSpec],
        install_parent: Path,
        *,
        command_runner: ToolCommandRunner | None = None,
    ) -> None:
        """Snapshot immutable tool requirements and their operator-owned cache."""
        self._tools = dict(tools)
        self._install_parent = install_parent
        self._command_runner = command_runner

    def before_ready(self, context: BeforeReadyContext) -> None:
        """Prepare verified tools before the sandbox is exposed to callers."""
        del context
        prepare_evaluator_tools(
            self._tools,
            self._install_parent,
            command_runner=self._command_runner,
        )


class _EvaluatorToolReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    spec: CargoGitToolSpec
    binaries: dict[str, str]


def cargo_install_argv(spec: CargoGitToolSpec, install_root: Path) -> tuple[str, ...]:
    """Build the exact Cargo invocation for an immutable Git tool."""
    binary_arguments = tuple(argument for binary in spec.bins for argument in ("--bin", binary))
    return (
        "cargo",
        "install",
        "--git",
        spec.git,
        "--rev",
        spec.rev,
        "--locked",
        "--root",
        str(install_root),
        *binary_arguments,
        spec.package,
    )


def prepare_evaluator_tools(
    tools: Mapping[str, CargoGitToolSpec],
    install_parent: Path,
    *,
    command_runner: ToolCommandRunner | None = None,
) -> dict[str, str]:
    """Install evaluator tools under a trusted content-addressed cache and return paths."""
    runner = command_runner or _run_cargo
    install_parent.mkdir(parents=True, exist_ok=True)
    replacements: dict[str, str] = {}
    for name, spec in tools.items():
        target = tool_install_root(install_parent, name, spec)
        if not _verified_install(target, spec):
            if target.exists():
                raise EvaluatorToolError(  # noqa: TRY003
                    f"evaluator tool installation failed receipt verification: {target}"
                )
            _install_tool(name, spec, target, runner)
        replacements.update(tool_path_replacements({name: spec}, install_parent))
    return replacements


def tool_path_replacements(
    tools: Mapping[str, CargoGitToolSpec], install_parent: Path
) -> dict[str, str]:
    """Map semantic tool tokens to binary paths below ``install_parent``."""
    return {
        tool_token(name, binary): str(
            tool_install_root(install_parent, name, spec) / "bin" / binary
        )
        for name, spec in tools.items()
        for binary in spec.bins
    }


def tool_spec_digest(spec: CargoGitToolSpec) -> str:
    """Return the content key for one normalized tool specification."""
    document = spec.model_dump(mode="json")
    encoded = json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def tool_install_root(install_parent: Path, name: str, spec: CargoGitToolSpec) -> Path:
    """Return the immutable cache root selected by the canonical tool specification."""
    return install_parent / name / tool_spec_digest(spec)


def _install_tool(
    name: str,
    spec: CargoGitToolSpec,
    target: Path,
    runner: ToolCommandRunner,
) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{target.name}-", dir=target.parent))
    try:
        try:
            result = runner(cargo_install_argv(spec, staging))
        except FileNotFoundError as exc:
            raise EvaluatorToolError(  # noqa: TRY003
                f"cannot install evaluator tool {name!r}: cargo was not found"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise EvaluatorToolError(  # noqa: TRY003
                f"cannot install evaluator tool {name!r}: cargo install timed out"
            ) from exc
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "cargo install failed").strip()[:500]
            raise EvaluatorToolError(  # noqa: TRY003
                f"cannot install evaluator tool {name!r}: {detail}"
            )
        binaries = _installed_binary_hashes(staging, spec)
        if binaries is None:
            raise EvaluatorToolError(  # noqa: TRY003
                f"cargo did not install every declared binary for evaluator tool {name!r}"
            )
        _write_receipt(
            staging,
            _EvaluatorToolReceipt(schema_version=1, spec=spec, binaries=binaries),
        )
        try:
            staging.replace(target)
        except OSError as exc:
            if not target.exists() or not _verified_install(target, spec):
                raise EvaluatorToolError(  # noqa: TRY003
                    f"cannot publish evaluator tool installation: {target}"
                ) from exc
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def _installed_binary_hashes(root: Path, spec: CargoGitToolSpec) -> dict[str, str] | None:
    hashes: dict[str, str] = {}
    for binary in spec.bins:
        binary_path = root / "bin" / binary
        if not binary_path.is_file() or not os.access(binary_path, os.X_OK):
            return None
        hashes[binary] = _file_sha256(binary_path)
    return hashes


def _verified_install(root: Path, spec: CargoGitToolSpec) -> bool:
    try:
        receipt = _EvaluatorToolReceipt.model_validate_json(
            (root / "receipt.json").read_text(encoding="utf-8")
        )
    except (OSError, ValidationError):
        return False
    hashes = _installed_binary_hashes(root, spec)
    return receipt.spec == spec and hashes is not None and receipt.binaries == hashes


def _write_receipt(root: Path, receipt: _EvaluatorToolReceipt) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=".receipt-", dir=root)
    temporary = Path(temporary_name)
    try:
        document = json.dumps(
            receipt.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(f"{document}\n")
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(root / "receipt.json")
    finally:
        temporary.unlink(missing_ok=True)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as binary:
        while chunk := binary.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _run_cargo(arguments: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        list(arguments),
        capture_output=True,
        check=False,
        text=True,
        timeout=600,
    )
