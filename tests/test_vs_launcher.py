"""Preparation-plan behavior of the repo-local ``vs`` launcher.

The launcher decides, before every interactive launch, whether to install
dependencies, regenerate the client's protocol types, and compile the client.
Getting that wrong is expensive in both directions: a missed step ships a stale
client, and a spurious step costs roughly 20s per launch. ``VS_PREPARE_PLAN``
exposes the decision without running any of the steps, so these tests exercise
it against synthetic trees rather than against this checkout.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = REPO_ROOT / "vs"


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """A minimal tree carrying every path the launcher's plan inspects."""
    (tmp_path / "vs").write_bytes(LAUNCHER.read_bytes())
    (tmp_path / "vs").chmod(0o755)
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "clients/tui/node_modules").mkdir(parents=True)
    _write(tmp_path / "package.json", '{"name": "root"}\n')
    _write(tmp_path / "pnpm-lock.yaml", "lockfileVersion: '9.0'\n")
    _write(tmp_path / "pnpm-workspace.yaml", "packages:\n  - clients/*\n")
    _write(tmp_path / "clients/tui/package.json", '{"name": "@vibesys/tui"}\n')
    _write(tmp_path / "clients/tui/tsconfig.json", "{}\n")
    _write(tmp_path / "clients/tui/tsconfig.check.json", "{}\n")
    _write(tmp_path / "clients/tui/src/index.ts", "export const client = 1;\n")
    _write(tmp_path / "clients/tui/src/generated/protocol.schema.json", "{}\n")
    _write(tmp_path / "clients/tui/dist/index.js", "export const client = 1;\n")
    for name in ("protocol", "events", "schema"):
        _write(tmp_path / f"src/vibesys/server/{name}.py", f"# {name}\n")
    # Backend implementation the client never sees.
    _write(tmp_path / "src/vibesys/server/service.py", "# service\n")
    return tmp_path


def _plan(tree: Path) -> list[str]:
    result = subprocess.run(  # tracked: #288
        ["./vs"],  # tracked: #288
        cwd=tree,
        env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "VS_PREPARE_PLAN": "1"},
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.split()


def _record(tree: Path) -> None:
    subprocess.run(  # tracked: #288
        ["./vs"],  # tracked: #288
        cwd=tree,
        env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "VS_PREPARE_PLAN": "record"},
        capture_output=True,
        text=True,
        check=True,
    )


def test_unprepared_tree_runs_every_step(tree: Path) -> None:
    """A tree that has never been prepared does all three steps, in order."""
    assert _plan(tree) == ["install", "protocol", "build"]


def test_prepared_tree_runs_nothing(tree: Path) -> None:
    """The repeat launch this issue is about: no install, no codegen, no tsc."""
    _record(tree)

    assert _plan(tree) == []


def test_backend_edit_outside_the_protocol_does_not_rebuild(tree: Path) -> None:
    _record(tree)

    (tree / "src/vibesys/server/service.py").write_text("# edited\n", encoding="utf-8")

    assert _plan(tree) == []


def test_protocol_edit_regenerates_types_and_rebuilds(tree: Path) -> None:
    _record(tree)

    (tree / "src/vibesys/server/protocol.py").write_text("# edited\n", encoding="utf-8")

    assert _plan(tree) == ["protocol", "build"]


@pytest.mark.parametrize("name", ["events.py", "schema.py"])
def test_every_protocol_source_triggers_regeneration(tree: Path, name: str) -> None:
    _record(tree)

    (tree / "src/vibesys/server" / name).write_text("# edited\n", encoding="utf-8")

    assert _plan(tree) == ["protocol", "build"]


def test_client_edit_rebuilds_without_reinstalling_or_regenerating(tree: Path) -> None:
    _record(tree)

    (tree / "clients/tui/src/index.ts").write_text("export const client = 2;\n", encoding="utf-8")

    assert _plan(tree) == ["build"]


def test_new_client_source_rebuilds(tree: Path) -> None:
    """The digest walks the tree, so an added file counts as a change."""
    _record(tree)

    _write(tree / "clients/tui/src/added.ts", "export const added = 1;\n")

    assert _plan(tree) == ["build"]


def test_lockfile_change_installs_and_rebuilds(tree: Path) -> None:
    _record(tree)

    (tree / "pnpm-lock.yaml").write_text("lockfileVersion: '9.1'\n", encoding="utf-8")

    assert _plan(tree) == ["install", "build"]


def test_touch_without_content_change_runs_nothing(tree: Path) -> None:
    """Timestamps are not the trigger: a checkout or pull must stay free."""
    _record(tree)

    for path in tree.rglob("*"):
        if path.is_file():
            path.touch()

    assert _plan(tree) == []


def test_deleted_build_output_rebuilds(tree: Path) -> None:
    _record(tree)

    (tree / "clients/tui/dist/index.js").unlink()

    assert _plan(tree) == ["build"]


def test_deleted_generated_schema_regenerates(tree: Path) -> None:
    _record(tree)

    (tree / "clients/tui/src/generated/protocol.schema.json").unlink()

    assert _plan(tree) == ["protocol", "build"]


def test_missing_dependencies_install_and_rebuild(tree: Path) -> None:
    _record(tree)

    (tree / "clients/tui/node_modules").rmdir()

    assert _plan(tree) == ["install", "build"]
