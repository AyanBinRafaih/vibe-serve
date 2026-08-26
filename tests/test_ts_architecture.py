"""Tests for the enforced TypeScript client dependency direction."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.check_ts_architecture import (  # pyright: ignore[reportMissingImports]
    architecture_errors,
)


def test_repository_typescript_packages_respect_architecture() -> None:
    root = Path(__file__).resolve().parents[1]

    assert architecture_errors(root) == []


def test_checker_rejects_reverse_and_ui_dependencies(tmp_path: Path) -> None:
    write_package(tmp_path, "backend-client", "@vibesys/backend-client", {})
    write_package(
        tmp_path,
        "core-state",
        "@vibesys/core-state",
        {"@vibesys/backend-client": "workspace:*", "@opentui/core": "1.0.0"},
        "import '@opentui/core';\nimport '@vibesys/tui';\n",
    )
    write_package(
        tmp_path,
        "tui",
        "@vibesys/tui",
        {"@vibesys/backend-client": "workspace:*", "@vibesys/core-state": "workspace:*"},
        "const next = {core: {...state.core, status: 'failed'}};\n",
    )

    errors = architecture_errors(tmp_path)

    assert any("core-state must not depend on @opentui/core" in error for error in errors)
    assert any("runtime- and UI-independent" in error for error in errors)
    assert any("core-state must not import @vibesys/tui" in error for error in errors)
    assert any("TUI production code must not reconstruct CoreState" in error for error in errors)


def write_package(
    root: Path,
    directory: str,
    name: str,
    dependencies: dict[str, str],
    source: str = "export {};\n",
) -> None:
    package = root / "clients" / directory
    (package / "src").mkdir(parents=True)
    (package / "package.json").write_text(json.dumps({"name": name, "dependencies": dependencies}))
    (package / "src" / "index.ts").write_text(source)
