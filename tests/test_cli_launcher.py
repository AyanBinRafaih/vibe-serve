"""Tests for the ``vibesys`` console entry point."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

from vibesys import cli


def _make_bundle(tmp_path):  # noqa: ANN001, ANN202  # tracked: #288
    tui = tmp_path / "_tui"
    runtime = tui / "bin" / "bun"
    launcher = tui / "app" / "dist" / "launcher.js"
    runtime.parent.mkdir(parents=True)
    launcher.parent.mkdir(parents=True)
    runtime.write_text("#!/bin/sh\n")
    runtime.chmod(0o755)
    launcher.write_text("// launcher\n")
    return cli.BundledTui(root=tui, runtime=runtime, launcher=launcher)


def _force_interactive(monkeypatch):  # noqa: ANN001, ANN202  # tracked: #288
    """Make `_headless_requested` return False regardless of the test's TTY."""
    monkeypatch.setattr(cli, "_headless_requested", lambda _args: False)


def test_bundled_tui_none_in_source_checkout():  # noqa: ANN201
    # The source tree ships no built _tui, so resolution returns None here.
    assert cli.bundled_tui() is None


def test_headless_requested_detects_flag_and_validate():  # noqa: ANN201  # tracked: #288
    assert cli._headless_requested(["--headless", "--input", "x"]) is True  # noqa: SLF001  # tracked: #288
    assert cli._headless_requested(["validate", "bundle"]) is True  # noqa: SLF001  # tracked: #288
    assert cli._headless_requested(["--help"]) is True  # noqa: SLF001  # tracked: #288
    assert cli._headless_requested(["-h"]) is True  # noqa: SLF001  # tracked: #288


def test_headless_requested_when_not_a_tty(monkeypatch):  # noqa: ANN001, ANN201  # tracked: #288
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(cli.sys.stdout, "isatty", lambda: True)
    assert cli._headless_requested(["--input", "x"]) is True  # noqa: SLF001  # tracked: #288


def test_headless_flag_runs_engine_subprocess(monkeypatch):  # noqa: ANN001, ANN201  # tracked: #288
    captured: dict[str, object] = {}

    def _call(cmd, env=None):  # noqa: ANN001, ANN202, ARG001  # tracked: #288
        captured["cmd"] = cmd
        return 0

    monkeypatch.setattr(cli.subprocess, "call", _call)

    rc = cli.main(["--headless", "--input", "bundle", "--local"])

    assert rc == 0
    assert captured["cmd"] == [
        sys.executable,
        "-m",
        "vibesys",
        "--headless",
        "--input",
        "bundle",
        "--local",
    ]


def test_interactive_execs_launcher_with_python_env(monkeypatch, tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    bundle = _make_bundle(tmp_path)
    _force_interactive(monkeypatch)
    monkeypatch.setattr(cli, "bundled_tui", lambda: bundle)

    captured: dict[str, object] = {}

    def _call(cmd, env=None):  # noqa: ANN001, ANN202  # tracked: #288
        captured["cmd"] = cmd
        captured["env"] = env
        return 0

    monkeypatch.setattr(cli.subprocess, "call", _call)

    with patch("shutil.which", side_effect=AssertionError("must not search system runtimes")):
        rc = cli.main(["--input", "bundle", "--local"])

    assert rc == 0
    assert captured["cmd"] == [
        str(bundle.runtime),
        str(bundle.launcher),
        "--input",
        "bundle",
        "--local",
    ]
    assert captured["env"]["VIBESYS_PYTHON"] == sys.executable  # pyright: ignore[reportIndexIssue]
    assert captured["env"]["VIBESYS_TUI_RUNTIME"] == str(bundle.runtime)  # pyright: ignore[reportIndexIssue]
    assert captured["env"]["BUN_CONFIG_SKIP_INSTALL_PACKAGES"] == "1"  # pyright: ignore[reportIndexIssue]


def test_interactive_with_non_executable_bundled_runtime_errors(  # noqa: ANN201
    monkeypatch,  # noqa: ANN001
    tmp_path,  # noqa: ANN001
    capsys,  # noqa: ANN001
):
    _force_interactive(monkeypatch)
    bundle = _make_bundle(tmp_path)
    bundle.runtime.chmod(0o644)
    monkeypatch.setattr(cli, "bundled_tui", lambda: bundle)

    with patch("shutil.which", side_effect=AssertionError("must not search system runtimes")):
        assert cli.main([]) == 1
    assert "bundled Bun runtime" in capsys.readouterr().err


def test_no_bundle_no_checkout_falls_back_to_headless(monkeypatch, capsys):  # noqa: ANN001, ANN201  # tracked: #288
    _force_interactive(monkeypatch)
    monkeypatch.setattr(cli, "bundled_tui", lambda: None)
    monkeypatch.setattr(cli, "source_checkout_root", lambda: None)
    captured: dict[str, object] = {}

    def _call(cmd, env=None):  # noqa: ANN001, ANN202, ARG001  # tracked: #288
        captured["cmd"] = cmd
        return 0

    monkeypatch.setattr(cli.subprocess, "call", _call)

    rc = cli.main(["--input", "bundle"])

    assert rc == 0
    assert captured["cmd"] == [sys.executable, "-m", "vibesys", "--input", "bundle"]
    assert "no source checkout" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Tier 2: build and run the TUI from a source checkout (subsumes ./vs)
# ---------------------------------------------------------------------------


def test_source_checkout_root_finds_this_repo():  # noqa: ANN201  # tracked: #288
    root = cli.source_checkout_root()
    assert root is not None
    assert (root / "clients" / "tui" / "package.json").is_file()


def test_source_checkout_builds_and_runs_launcher(monkeypatch, tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    _force_interactive(monkeypatch)
    monkeypatch.setattr(cli, "bundled_tui", lambda: None)
    monkeypatch.setattr(cli, "source_checkout_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "_bun_executable", lambda: Path("/usr/bin/bun"))
    monkeypatch.setattr(cli, "_node_executable", lambda: Path("/usr/bin/node"))
    monkeypatch.setattr(cli, "_node_major", lambda _node: 20)
    build_calls: list[bool] = []
    monkeypatch.setattr(cli, "_needs_rebuild", lambda _root: True)
    monkeypatch.setattr(
        cli, "_ensure_source_tui_built", lambda _root: build_calls.append(True) or True
    )

    captured: dict[str, object] = {}

    def _call(cmd, cwd=None, env=None):  # noqa: ANN001, ANN202  # tracked: #288
        captured["cmd"] = cmd
        captured["cwd"] = cwd
        captured["env"] = env
        return 0

    monkeypatch.setattr(cli.subprocess, "call", _call)

    rc = cli.main(["--input", "bundle", "--local"])

    assert rc == 0
    assert build_calls == [True]  # a stale checkout was rebuilt
    launcher = tmp_path / "clients" / "tui" / "dist" / "launcher.js"
    assert captured["cmd"] == ["/usr/bin/node", str(launcher), "--input", "bundle", "--local"]
    assert captured["cwd"] == str(tmp_path)
    assert captured["env"]["VIBESYS_PYTHON"] == sys.executable  # pyright: ignore[reportIndexIssue]


def test_source_checkout_skips_build_when_fresh(monkeypatch, tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    _force_interactive(monkeypatch)
    monkeypatch.setattr(cli, "bundled_tui", lambda: None)
    monkeypatch.setattr(cli, "source_checkout_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "_bun_executable", lambda: Path("/usr/bin/bun"))
    monkeypatch.setattr(cli, "_node_executable", lambda: Path("/usr/bin/node"))
    monkeypatch.setattr(cli, "_node_major", lambda _node: 22)
    monkeypatch.setattr(cli, "_needs_rebuild", lambda _root: False)

    message = "must not rebuild a fresh checkout"

    def _boom(_root):  # noqa: ANN001, ANN202  # tracked: #288
        raise AssertionError(message)

    monkeypatch.setattr(cli, "_ensure_source_tui_built", _boom)
    monkeypatch.setattr(cli.subprocess, "call", lambda *a, **k: 0)  # noqa: ARG005  # tracked: #288

    assert cli.main([]) == 0


def test_source_checkout_missing_bun_errors(monkeypatch, tmp_path, capsys):  # noqa: ANN001, ANN201  # tracked: #288
    _force_interactive(monkeypatch)
    monkeypatch.setattr(cli, "bundled_tui", lambda: None)
    monkeypatch.setattr(cli, "source_checkout_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "_bun_executable", lambda: None)

    assert cli.main([]) == 1
    assert "Bun is required" in capsys.readouterr().err


def test_source_checkout_old_node_errors(monkeypatch, tmp_path, capsys):  # noqa: ANN001, ANN201  # tracked: #288
    _force_interactive(monkeypatch)
    monkeypatch.setattr(cli, "bundled_tui", lambda: None)
    monkeypatch.setattr(cli, "source_checkout_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "_bun_executable", lambda: Path("/usr/bin/bun"))
    monkeypatch.setattr(cli, "_node_executable", lambda: Path("/usr/bin/node"))
    monkeypatch.setattr(cli, "_node_major", lambda _node: 18)

    assert cli.main([]) == 1
    assert "Node.js 20+" in capsys.readouterr().err
