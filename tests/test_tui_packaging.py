"""Tests for the wheel-time TUI build/stage helper."""

from __future__ import annotations

import os
import shutil
from pathlib import Path  # tracked: #288

import pytest

# `tui_packaging` is a repo-root, build-time module (imported by setup.py). It is
# not part of the installed package, so pyright's project roots cannot resolve
# it; pytest picks it up via `pythonpath = ["."]`.
from tui_packaging import (  # pyright: ignore[reportMissingImports]
    build_and_stage_tui,
    build_command,
    detect_package_manager,
    install_command,
    prune_command,
)


def _which_only(*available: str):  # noqa: ANN202  # tracked: #288
    present = set(available)
    return lambda name: f"/usr/bin/{name}" if name in present else None


def test_detect_prefers_bun_over_npm():  # noqa: ANN201  # tracked: #288
    assert detect_package_manager(which=_which_only("bun", "npm")) == "bun"


def test_detect_falls_back_to_npm():  # noqa: ANN201  # tracked: #288
    assert detect_package_manager(which=_which_only("npm")) == "npm"


def test_detect_returns_none_when_no_toolchain():  # noqa: ANN201  # tracked: #288
    assert detect_package_manager(which=_which_only()) is None


@pytest.mark.parametrize(
    ("manager", "install_has", "prune_has"),
    [
        ("bun", "install", "--production"),
        ("npm", "install", "--omit=dev"),
    ],
)
def test_command_plans(manager, install_has, prune_has):  # noqa: ANN001, ANN201  # tracked: #288
    assert install_command(manager)[0] == manager
    assert install_has in install_command(manager)
    assert build_command(manager) == (manager, "run", "build")
    assert prune_has in prune_command(manager)


def test_build_skips_when_no_toolchain(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    repo = tmp_path / "repo"
    (repo / "clients" / "tui").mkdir(parents=True)
    (repo / "clients" / "tui" / "package.json").write_text("{}\n")
    dest = tmp_path / "staged"

    message = "runner must not be called without a toolchain"

    def _boom(*_args, **_kwargs):  # noqa: ANN002, ANN003, ANN202  # tracked: #288
        raise AssertionError(message)

    ok = build_and_stage_tui(repo, dest, which=_which_only(), runner=_boom)

    assert ok is False
    assert not dest.exists()


def test_build_skips_when_no_tui_project(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    repo = tmp_path / "repo"
    repo.mkdir()
    ok = build_and_stage_tui(repo, tmp_path / "staged", which=_which_only("npm"))
    assert ok is False


def test_build_returns_false_when_build_fails(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    import subprocess  # noqa: PLC0415  # tracked: #288

    repo = tmp_path / "repo"
    (repo / "clients" / "tui").mkdir(parents=True)
    (repo / "clients" / "tui" / "package.json").write_text("{}\n")

    def _fail(cmd, **_kwargs):  # noqa: ANN001, ANN003, ANN202  # tracked: #288
        raise subprocess.CalledProcessError(1, cmd)

    ok = build_and_stage_tui(repo, tmp_path / "staged", which=_which_only("npm"), runner=_fail)
    assert ok is False


def test_build_stages_dist_and_strips_maps(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    repo = tmp_path / "repo"
    tui = repo / "clients" / "tui"
    tui.mkdir(parents=True)
    (tui / "package.json").write_text('{"name": "@vibesys/tui"}\n')

    calls: list[list[str]] = []

    def _runner(cmd, **_kwargs):  # noqa: ANN001, ANN003, ANN202  # tracked: #288
        calls.append(cmd)
        # Simulate the build emitting dist/ + node_modules on first invocation.
        dist = tui / "dist"
        dist.mkdir(exist_ok=True)
        (dist / "launcher.js").write_text("// launcher\n")
        (dist / "launcher.js.map").write_text("{}\n")
        nm = tui / "node_modules" / "@opentui" / "core"
        nm.mkdir(parents=True, exist_ok=True)
        (nm / "index.js").write_text("// core\n")
        (nm / "index.js.map").write_text("{}\n")

    dest = tmp_path / "staged"
    ok = build_and_stage_tui(repo, dest, which=_which_only("npm"), runner=_runner)

    assert ok is True
    # install, build, prune all ran.
    assert [c[:2] for c in calls] == [["npm", "install"], ["npm", "run"], ["npm", "prune"]]
    assert (dest / "dist" / "launcher.js").is_file()
    assert (dest / "package.json").is_file()
    assert (dest / "node_modules" / "@opentui" / "core" / "index.js").is_file()
    # Source maps were stripped from both dist and node_modules.
    assert not (dest / "dist" / "launcher.js.map").exists()
    assert not (dest / "node_modules" / "@opentui" / "core" / "index.js.map").exists()


@pytest.mark.skipif(
    not (os.environ.get("VIBESYS_TEST_TUI_BUILD") and shutil.which("npm")),
    reason="set VIBESYS_TEST_TUI_BUILD=1 and have npm to run the real TUI build",
)
def test_real_build_end_to_end(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    repo_root = Path(__file__).resolve().parents[1]
    dest = tmp_path / "staged"

    ok = build_and_stage_tui(repo_root, dest)

    assert ok is True
    assert (dest / "dist" / "launcher.js").is_file()
    assert (dest / "node_modules" / "@opentui" / "core").is_dir()
