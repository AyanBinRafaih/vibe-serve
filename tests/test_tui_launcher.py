"""Tests for the ``vibesys-tui`` console entry point."""

from __future__ import annotations

import sys

from vibesys import tui_launcher


def _make_bundle(tmp_path):  # noqa: ANN001, ANN202  # tracked: #288
    tui = tmp_path / "_tui"
    (tui / "dist").mkdir(parents=True)
    (tui / "dist" / "launcher.js").write_text("// launcher\n")
    return tui


def test_bundled_tui_dir_none_in_source_checkout():  # noqa: ANN201  # tracked: #288
    # The source tree ships no built _tui, so resolution returns None here.
    assert tui_launcher.bundled_tui_dir() is None


def test_main_errors_without_bundle(monkeypatch, capsys):  # noqa: ANN001, ANN201  # tracked: #288
    monkeypatch.setattr(tui_launcher, "bundled_tui_dir", lambda: None)

    assert tui_launcher.main([]) == 1
    assert "not bundled" in capsys.readouterr().err


def test_main_errors_without_runtime(monkeypatch, capsys, tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    monkeypatch.setattr(tui_launcher, "bundled_tui_dir", lambda: _make_bundle(tmp_path))
    monkeypatch.setattr(tui_launcher.shutil, "which", lambda _name: None)

    assert tui_launcher.main([]) == 1
    assert "JavaScript runtime is required" in capsys.readouterr().err


def test_main_execs_launcher_with_python_env(monkeypatch, tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    tui = _make_bundle(tmp_path)
    monkeypatch.setattr(tui_launcher, "bundled_tui_dir", lambda: tui)
    monkeypatch.setattr(
        tui_launcher.shutil, "which", lambda name: "/usr/bin/bun" if name == "bun" else None
    )

    captured: dict[str, object] = {}

    def _call(cmd, env=None):  # noqa: ANN001, ANN202  # tracked: #288
        captured["cmd"] = cmd
        captured["env"] = env
        return 0

    monkeypatch.setattr(tui_launcher.subprocess, "call", _call)

    rc = tui_launcher.main(["--input", "bundle", "--local"])

    assert rc == 0
    assert captured["cmd"] == [
        "bun",
        str(tui / "dist" / "launcher.js"),
        "--input",
        "bundle",
        "--local",
    ]
    assert captured["env"]["VIBESYS_PYTHON"] == sys.executable  # pyright: ignore[reportIndexIssue]


def test_main_prefers_bun_then_node(monkeypatch, tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    tui = _make_bundle(tmp_path)
    monkeypatch.setattr(tui_launcher, "bundled_tui_dir", lambda: tui)
    # Only node available -> launcher runs under node.
    monkeypatch.setattr(
        tui_launcher.shutil, "which", lambda name: "/usr/bin/node" if name == "node" else None
    )
    captured: dict[str, object] = {}

    def _call(cmd, env=None):  # noqa: ANN001, ANN202, ARG001  # tracked: #288
        captured["cmd"] = cmd
        return 0

    monkeypatch.setattr(tui_launcher.subprocess, "call", _call)

    tui_launcher.main([])

    assert captured["cmd"][0] == "node"  # pyright: ignore[reportIndexIssue]
