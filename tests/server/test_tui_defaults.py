"""TUI defaults over the control channel: resolution, caching, and wiring."""

from __future__ import annotations

from pathlib import Path  # noqa: TC003  # tracked: #288
from typing import Any
from unittest.mock import patch

import pytest

from vibesys.main import _tui_defaults_from_argv, main
from vibesys.repository import InteractiveSetupDefaults, RepositoryVisibility
from vibesys.server.protocol import TuiDefaultsQuery
from vibesys.server.runtime import run_server
from vibesys.server.service import SupervisionService
from vibesys.server.supervisor import RunSupervisor
from vibesys.tui import TuiTheme


def _defaults(theme: TuiTheme) -> InteractiveSetupDefaults:
    return InteractiveSetupDefaults(
        runs_dir="/runs",
        input_path="",
        experiment_name="experiment-1",
        repository_owner=None,
        repository_name="experiment-1",
        visibility=RepositoryVisibility.PRIVATE,
        theme=theme,
    )


def test_service_answers_with_the_provider_resolved_defaults() -> None:
    service = SupervisionService(
        RunSupervisor(), tui_defaults=lambda: _defaults(TuiTheme.SOLARIZED_LIGHT)
    )

    response = service.execute(TuiDefaultsQuery())

    assert response.ok
    assert response.tui_defaults is not None
    assert response.tui_defaults.theme == TuiTheme.SOLARIZED_LIGHT


def test_service_resolves_defaults_on_demand_and_caches_them() -> None:
    calls: list[int] = []

    def provide() -> InteractiveSetupDefaults:
        calls.append(1)
        return _defaults(TuiTheme.LIGHT)

    service = SupervisionService(RunSupervisor(), tui_defaults=provide)
    assert calls == []

    first = service.execute(TuiDefaultsQuery())
    second = service.execute(TuiDefaultsQuery())

    assert calls == [1]
    assert first.tui_defaults == second.tui_defaults


def test_service_without_a_provider_reports_no_defaults() -> None:
    response = SupervisionService(RunSupervisor()).execute(TuiDefaultsQuery())

    assert response.ok
    assert response.tui_defaults is None


def test_a_failing_provider_surfaces_as_a_request_error() -> None:
    """An unloadable configuration is a request failure, not a bad default."""

    def provide() -> InteractiveSetupDefaults:
        raise FileNotFoundError("agent.toml is missing")  # noqa: TRY003  # tracked: #288

    service = SupervisionService(RunSupervisor(), tui_defaults=provide)

    # The transport turns this into ``ok=False`` like any other query error.
    with pytest.raises(FileNotFoundError):
        service.execute(TuiDefaultsQuery())


def test_run_server_hands_the_provider_to_the_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}
    provider = lambda: _defaults(TuiTheme.DARK)  # noqa: E731

    class _RecordingService(SupervisionService):
        def __init__(self, supervisor: RunSupervisor, **kwargs: Any) -> None:  # noqa: ANN401
            captured.update(kwargs)
            super().__init__(supervisor, **kwargs)

    class _FakeSocketServer:
        def __init__(self, socket_path: Path, service: SupervisionService) -> None:
            self.socket_path = socket_path
            self.service = service

        def __enter__(self) -> _FakeSocketServer:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def wait_for_subscriber(self, timeout: float) -> bool:
            del timeout
            return True

        def wait_for_subscriber_disconnect(self) -> None:
            return None

    monkeypatch.setattr("vibesys.server.runtime.SupervisionService", _RecordingService)
    monkeypatch.setattr("vibesys.server.runtime.SupervisionSocketServer", _FakeSocketServer)

    value = run_server(lambda: "ran", socket_path=tmp_path / "control.sock", tui_defaults=provider)

    assert value == "ran"
    assert captured["tui_defaults"] is provider


def test_provider_resolves_the_theme_from_the_launch_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "agent.toml").write_text(
        '[model]\nname = "gpt-5.5"\n[tui]\ntheme = "catppuccin-mocha"\n'
    )
    monkeypatch.chdir(tmp_path)

    defaults = _tui_defaults_from_argv(["--stub-agent", "--headless"])()

    assert defaults.theme == TuiTheme.CATPPUCCIN_MOCHA
    # Directory-only resolution never shells out for a repository owner.
    assert defaults.repository_owner is None


def test_provider_honors_an_explicit_config_and_theme(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "elsewhere.toml"
    config.write_text('[model]\nname = "gpt-5.5"\n[tui]\ntheme = "light"\n')
    monkeypatch.chdir(tmp_path)

    from_config = _tui_defaults_from_argv(["--config", str(config)])()
    from_flag = _tui_defaults_from_argv([f"--config={config}", "--theme", "high-contrast-dark"])()

    assert from_config.theme == TuiTheme.LIGHT
    assert from_flag.theme == TuiTheme.HIGH_CONTRAST_DARK


def test_control_socket_launch_passes_a_defaults_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "agent.toml").write_text('[model]\nname = "gpt-5.5"\n[tui]\ntheme = "light"\n')
    monkeypatch.chdir(tmp_path)
    captured: dict[str, Any] = {}

    def fake_run_server(run: Any, **kwargs: Any) -> None:  # noqa: ANN401
        del run
        captured.update(kwargs)

    monkeypatch.setattr("vibesys.server.runtime.run_server", fake_run_server)
    argv = ["vibesys", "--stub-agent", "--headless", "--control-socket", str(tmp_path / "c.sock")]
    with patch("sys.argv", argv):
        main()

    assert captured["socket_path"] == tmp_path / "c.sock"
    assert captured["tui_defaults"]().theme == TuiTheme.LIGHT
