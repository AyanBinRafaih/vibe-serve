"""Tests for the ``vibesys`` console entry point."""

from __future__ import annotations

import sys

from vibesys import cli


def _make_bundle(tmp_path):  # noqa: ANN001, ANN202  # tracked: #288
    tui = tmp_path / "_tui"
    (tui / "dist").mkdir(parents=True)
    (tui / "dist" / "launcher.js").write_text("// launcher\n")
    return tui


def _force_interactive(monkeypatch):  # noqa: ANN001, ANN202  # tracked: #288
    """Make `_headless_requested` return False regardless of the test's TTY."""
    monkeypatch.setattr(cli, "_headless_requested", lambda _args: False)


def test_bundled_tui_dir_none_in_source_checkout():  # noqa: ANN201  # tracked: #288
    # The source tree ships no built _tui, so resolution returns None here.
    assert cli.bundled_tui_dir() is None


def test_headless_requested_detects_flag_and_validate():  # noqa: ANN201  # tracked: #288
    assert cli._headless_requested(["--headless", "--input", "x"]) is True  # noqa: SLF001  # tracked: #288
    assert cli._headless_requested(["validate", "bundle"]) is True  # noqa: SLF001  # tracked: #288


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
    tui = _make_bundle(tmp_path)
    _force_interactive(monkeypatch)
    monkeypatch.setattr(cli, "bundled_tui_dir", lambda: tui)
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/usr/bin/bun" if name == "bun" else None)

    captured: dict[str, object] = {}

    def _call(cmd, env=None):  # noqa: ANN001, ANN202  # tracked: #288
        captured["cmd"] = cmd
        captured["env"] = env
        return 0

    monkeypatch.setattr(cli.subprocess, "call", _call)

    rc = cli.main(["--input", "bundle", "--local"])

    assert rc == 0
    assert captured["cmd"] == [
        "bun",
        str(tui / "dist" / "launcher.js"),
        "--input",
        "bundle",
        "--local",
    ]
    assert captured["env"]["VIBESYS_PYTHON"] == sys.executable  # pyright: ignore[reportIndexIssue]


def test_interactive_prefers_bun_then_node(monkeypatch, tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    tui = _make_bundle(tmp_path)
    _force_interactive(monkeypatch)
    monkeypatch.setattr(cli, "bundled_tui_dir", lambda: tui)
    # Only node available -> launcher runs under node.
    monkeypatch.setattr(
        cli.shutil, "which", lambda name: "/usr/bin/node" if name == "node" else None
    )
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        cli.subprocess,
        "call",
        lambda cmd, env=None: captured.setdefault("cmd", cmd) or 0,  # noqa: ARG005  # tracked: #288
    )

    cli.main([])

    assert captured["cmd"][0] == "node"  # pyright: ignore[reportIndexIssue]


def test_interactive_without_runtime_errors(monkeypatch, tmp_path, capsys):  # noqa: ANN001, ANN201  # tracked: #288
    _force_interactive(monkeypatch)
    monkeypatch.setattr(cli, "bundled_tui_dir", lambda: _make_bundle(tmp_path))
    monkeypatch.setattr(cli.shutil, "which", lambda _name: None)

    assert cli.main([]) == 1
    assert "JavaScript runtime is required" in capsys.readouterr().err


def test_interactive_without_bundle_falls_back_to_headless(monkeypatch, capsys):  # noqa: ANN001, ANN201  # tracked: #288
    _force_interactive(monkeypatch)
    monkeypatch.setattr(cli, "bundled_tui_dir", lambda: None)
    captured: dict[str, object] = {}

    def _call(cmd, env=None):  # noqa: ANN001, ANN202, ARG001  # tracked: #288
        captured["cmd"] = cmd
        return 0

    monkeypatch.setattr(cli.subprocess, "call", _call)

    rc = cli.main(["--input", "bundle"])

    assert rc == 0
    assert captured["cmd"] == [sys.executable, "-m", "vibesys", "--input", "bundle"]
    assert "not bundled" in capsys.readouterr().err
